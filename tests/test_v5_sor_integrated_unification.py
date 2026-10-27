"""V5 chart source contract, exercised without a broker or running GUI."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from gui_indicator_follow_signal_validation_v5_window import (
    _integrated_display_with_krx_prefix,
    _krx_regular_session_candles,
)
from tests.test_indicator_follow_signal_validation_v5_integrated_history_scroll import (
    _flow, _session,
)
from tests import test_indicator_follow_v2_display_only_integrated_flow as contracts
from tests import test_indicator_follow_signal_validation_v5_baseline as baseline


def _selected_sor_flow():
    """Projection unit cases start after _run_validation has verified selection."""
    flow = _flow()
    flow._validation_source_by_code[_session().request.stock.code] = True
    flow._now_factory = lambda: datetime(2026, 10, 23, 16, 0, tzinfo=timezone(timedelta(hours=9)))
    return flow


@pytest.fixture
def frozen_sor_plan():
    # Importing a helper does not activate its module's autouse plan fixture.
    from tests.test_display_only_integrated_history import _frozen_source_plan

    with _frozen_source_plan() as root:
        yield root


def _row(stamp, close):
    return {"time": stamp, "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 10}


def _pool(session, candles, *, source="INTEGRATED", identity="005930_AL"):
    return {
        "stock": session.request.stock, "candles": candles,
        "market_source": source, "market_data_identity": identity,
        "timeframe_key": "M1", "timeframe_minutes": 1,
        "requested_count": len(candles), "request_id": "verified-al",
    }


def _proved_pre(session, rows, *, as_of="2026-10-23T08:45:00+09:00"):
    return {
        "stock_code": session.request.stock.code,
        "timeframe_key": session.request.timeframe_key,
        "timeframe_minutes": session.request.timeframe_minutes,
        "market_data_identity": "005930_AL",
        "market_source": "INTEGRATED",
        "display_only": True,
        "verified_nxt_eligible": True,
        "request_as_of": as_of,
        "candles": rows,
    }


@pytest.mark.parametrize("minutes", [3, 5])
def test_pre_native_minute_keeps_al_history_without_a_second_nx_request(tmp_path, monkeypatch, minutes):
    from tests.test_v5_nx_live_history_preservation import _run_with_al

    flow, session, pool, _ = _run_with_al(tmp_path, monkeypatch, minutes)
    flow._broker.request_minute_candles_display_only_nxt = lambda *_a, **_k: pytest.fail(
        "NX must keep the primary receipt/forming/cache path, not a second sidecar"
    )
    flow._ensure_integrated_display_sidecar(object(), session, 5000)
    assert not flow._integrated_display_inflight
    view = flow._validation_pool_for_session(session, pool, target_count=5000)
    assert view["candles"][-1]["close"] == 123
    assert view["display_candles"][0]["close"] == 500
    assert len(view["display_candles"]) == 5000
    assert all(row["close"] != 500 for row in view["candles"])

    krx_flow = _flow()
    stock = session.request.stock
    krx_flow._validation_source_by_code[stock.code] = False
    krx_flow._validation_frozen_krx_codes.add(stock.code)
    krx = [_row("20261023090000", 100)]
    krx_pool = dict(stock=stock, candles=krx, market_source="KRX",
                    market_data_identity=stock.code, timeframe_key=f"M{minutes}",
                    timeframe_minutes=minutes, requested_count=1, request_id="krx-native")
    krx_view = krx_flow._validation_pool_for_session(session, krx_pool, target_count=5000)
    assert krx_view["candles"] == krx
    assert krx_view.get("display_candles", krx) == krx


def test_verified_nxt_pre_missing_from_sor_enters_one_composite_view():
    flow, session = _selected_sor_flow(), _session()
    sor = [_row("20261023090000", 110), _row("20261023090100", 120)]
    pre = _row("20261023083000", 80)
    pool = _pool(session, sor)
    original = deepcopy(pool)
    view = flow._validation_pool_for_session(
        session, pool, target_count=5000,
        sidecar_override=_proved_pre(session, [pre]),
    )

    assert view["candles"] == [pre, *sor]
    assert view["display_candles"] == view["candles"]
    assert view["market_source"] == "VALIDATION_COMPOSITE"
    assert view["market_data_identity"] == "005930_AL_NXT_PRE_VALIDATION"
    assert view["snapshot"].market_source == "VALIDATION_COMPOSITE"
    assert view["snapshot"].to_rows()[-1]
    assert pool == original


def test_sor_pre_row_wins_over_sidecar_duplicate():
    flow, session = _selected_sor_flow(), _session()
    sor_pre = _row("20261023083000", 80)
    sor = [sor_pre, _row("20261023090000", 110)]
    view = flow._validation_pool_for_session(
        session, _pool(session, sor), target_count=5000,
        sidecar_override=_proved_pre(session, [_row("20261023083000", 999)]),
    )
    assert view["candles"] == sor
    assert view["market_source"] == "INTEGRATED"


def test_sor_pre_outside_selected_tail_is_not_reintroduced_from_sidecar():
    flow, session = _selected_sor_flow(), _session()
    sor = [_row("20261023083000", 80), _row("20261023090000", 110)]
    view = flow._validation_pool_for_session(
        session, _pool(session, sor), target_count=1,
        sidecar_override=_proved_pre(session, [_row("20261023083000", 999)]),
    )
    assert view["candles"] == [sor[-1]]
    assert view["market_source"] == "INTEGRATED"


@pytest.mark.parametrize("change", [
    {"stock_code": "000660"},
    {"timeframe_key": "M3"},
    {"market_source": "KRX"},
    {"verified_nxt_eligible": False},
    {"request_as_of": "2026-10-23T08:20:00+09:00"},
    {"request_as_of": None},
])
def test_unproved_nxt_pre_leaves_valid_sor_chart_intact(change):
    flow, session = _selected_sor_flow(), _session()
    sor = [_row("20261023090000", 110)]
    sidecar = _proved_pre(session, [_row("20261023083000", 80)])
    sidecar.update(change)
    view = flow._validation_pool_for_session(
        session, _pool(session, sor), target_count=5000,
        sidecar_override=sidecar,
    )
    assert view["candles"] == sor
    assert view["market_source"] == "INTEGRATED"


def test_sidecar_regular_prices_never_replace_sor_and_regular_only_is_display_only():
    flow, session = _selected_sor_flow(), _session()
    sor = [_row("20261023090000", 110), _row("20261023090100", 120)]
    pre = _row("20261023083000", 80)
    sidecar = _proved_pre(session, [pre, _row("20261023090000", 999)])
    settings = contracts.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1},
        "validation_timeframe": {"key": "M1"},
        "validation_market_scope": {"regular_market_only": True},
    })
    regular_session = contracts.ValidationSession(
        contracts.ValidationRequest(session.request.stock, settings, 1),
        operation_active_reader=lambda: False,
    )
    view = flow._validation_pool_for_session(
        regular_session, _pool(regular_session, sor), target_count=5000,
        sidecar_override=sidecar,
    )
    assert view["candles"] == [pre, *sor]
    assert view["display_candles"] == sor


def test_duplicate_or_invalid_premarket_fails_closed():
    flow, session = _selected_sor_flow(), _session()
    sor = [_row("20261023090000", 110)]
    pre = _row("20261023083000", 80)
    for rows in ([pre, pre], [{**pre, "high": 70}]):
        view = flow._validation_pool_for_session(
            session, _pool(session, sor), target_count=5000,
            sidecar_override=_proved_pre(session, rows),
        )
        assert view["candles"] == sor


def test_future_request_time_cannot_prove_premarket():
    flow, session = _selected_sor_flow(), _session()
    flow._now_factory = lambda: datetime(2026, 10, 23, 8, 20, tzinfo=timezone(timedelta(hours=9)))
    sor = [_row("20261023090000", 110)]
    view = flow._validation_pool_for_session(
        session, _pool(session, sor), target_count=5000,
        sidecar_override=_proved_pre(session, [_row("20261023083000", 80)]),
    )
    assert view["candles"] == sor


def test_trusted_sor_requests_detached_pre_only_for_verified_nxt(monkeypatch):
    flow, session = _flow(), _session()
    flow._validation_source_by_code[session.request.stock.code] = True
    flow._now_factory = lambda: datetime(2026, 10, 23, 8, 45, tzinfo=timezone(timedelta(hours=9)))
    pre = _row("20261023083000", 80)
    calls = []

    def request(code, name, **kwargs):
        calls.append((code, kwargs["interval"], kwargs["nxt_available"]))
        kwargs["callback"]({
            "ok": True, "code": code, "interval": 1,
            "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
            "display_only": True, "rows": flow._historical_rows_from_candles([pre]),
        })
        return {"ok": True}

    flow._broker = SimpleNamespace(request_minute_candles_display_only_integrated=request)
    monkeypatch.setattr(flow, "_verified_nxt_display_stock", lambda _stock: True)
    window = SimpleNamespace(marking_display_check=SimpleNamespace(isChecked=lambda: False))
    flow._ensure_integrated_display_sidecar(window, session, 5000)

    assert calls == [("005930", 1, True)]
    cached = flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)]
    assert flow._verified_validation_premarket_candles(session, cached) == [pre]


def test_sor_tail_is_proved_before_nxt_pre_is_composed():
    from tests.test_v5_sor_bundle_c import prepared

    flow, session, raw, identity, calls = prepared("M1")
    flow._validation_source_by_code[session.request.stock.code] = True
    pre = _row("20261020083000", 80)
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = (
        _proved_pre(session, [pre], as_of="2026-10-20T08:45:00+09:00")
    )
    view = flow._validation_pool_for_session(session, raw, target_count=5000)
    result = flow._verified_sor_view_for_session(
        session, raw, view, flow._now_factory(), identity,
    )

    assert calls == [identity[-1]]
    assert pre in result["candles"]
    assert result["sor_tail_calculation_complete"] is True
    assert result["snapshot"].market_source == "VALIDATION_COMPOSITE"
    assert result["snapshot"].market_data_identity == "005930_AL_NXT_PRE_VALIDATION"
    assert result["snapshot"].to_rows()[-1]


def test_sor_calculation_and_display_share_full_integrated_history_and_forming_minute():
    flow, session = _selected_sor_flow(), _session()
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110),
                  _row("20261023090100", 120)]  # latest M1 is still forming
    pool = _pool(session, integrated)
    original = deepcopy(pool)
    view = flow._validation_pool_for_session(session, pool, target_count=5000)

    assert view["market_source"] == "INTEGRATED"
    assert view["market_data_identity"] == "005930_AL"
    assert view["candles"] == integrated
    assert view.get("display_candles", view["candles"]) == integrated
    assert view["candles"][-1]["time"] == "20261023090100"
    assert pool == original


def test_sor_ignores_old_display_sidecar_even_when_it_contains_krx_values():
    flow, session = _selected_sor_flow(), _session()
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110)]
    old = [_row("20261023080000", 900), _row("20261023090000", 910)]
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = {
        "candles": old, "verified_nxt_eligible": True,
    }
    view = flow._validation_pool_for_session(
        session, _pool(session, integrated), target_count=5000,
    )
    assert view["candles"] == integrated
    assert view.get("display_candles", view["candles"]) == integrated
    assert view.get("display_only_integrated") is not True


def test_trusted_sor_ignores_unproved_non_premarket_sidecar(monkeypatch):
    flow, session = _selected_sor_flow(), _session()
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110),
                  _row("20261023154000", 120)]
    sidecar = {
        "candles": [_row("20261023070000", 900), _row("20261023160000", 910)],
        "verified_nxt_eligible": True,
    }
    pool = _pool(session, integrated)

    def unexpected_projection(*_args):
        pytest.fail("trusted SOR must not consult after-hours or KRX projection")

    monkeypatch.setattr(flow, "_verified_validation_aftermarket_candles", unexpected_projection)
    monkeypatch.setattr(flow, "_integrated_regular_bucket_conflicts", unexpected_projection)

    view = flow._validation_pool_for_session(
        session, pool, target_count=2, sidecar_override=sidecar,
    )
    assert view["candles"] == integrated[-2:]
    assert view["display_candles"] == integrated[-2:]
    assert view["requested_count"] == 2
    assert view["market_source"] == "INTEGRATED"
    assert view["market_data_identity"] == "005930_AL"
    assert view["source_requested_count"] == 3
    assert view["snapshot"].market_source == "INTEGRATED"
    assert view.get("validation_composite_calculation") is not True
    assert view.get("validation_premarket_calculation") is not True
    assert view.get("validation_aftermarket_calculation") is not True
    assert pool["candles"] == integrated


def test_sor_replaces_stale_display_metadata_with_its_calculation_source():
    flow, session = _selected_sor_flow(), _session()
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110)]
    pool = _pool(session, integrated)
    pool["display_candles"] = [_row("20261023090000", 999)]
    pool["display_only_integrated"] = True
    pool["validation_composite_calculation"] = True

    view = flow._validation_pool_for_session(session, pool, target_count=5000)
    assert view["candles"] == integrated
    assert view["display_candles"] == integrated
    assert view.get("display_only_integrated") is not True
    assert view.get("validation_composite_calculation") is not True
    assert pool["display_candles"][0]["close"] == 999


def test_regular_session_checkbox_filters_display_only_and_keeps_nxt_calculation():
    flow = _selected_sor_flow()
    stock = _session().request.stock
    settings = contracts.ValidationSettingsSnapshot({
        "bar": {"bar_minutes": 1},
        "validation_timeframe": {"key": "M1"},
        "validation_market_scope": {"regular_market_only": True},
    })
    session = contracts.ValidationSession(
        contracts.ValidationRequest(stock, settings, 1),
        operation_active_reader=lambda: False,
    )
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110),
                  _row("20261023090100", 120), _row("20261023154000", 130)]
    view = flow._validation_pool_for_session(
        session, _pool(session, integrated), target_count=5000,
    )
    assert view["candles"] == integrated
    assert view["display_candles"] == integrated[1:3]
    assert _krx_regular_session_candles(integrated) == integrated[1:3]
    assert view["market_source"] == "INTEGRATED"


@pytest.mark.parametrize("source,identity", [
    ("KRX", "005930"), ("KRX", "005930_AL"),
    ("INTEGRATED", "005930"), ("INTEGRATED", "000660_AL"),
    ("integrated", "005930_AL"),
])
def test_sor_rejects_krx_or_mismatched_al_identity(source, identity):
    flow, session = _selected_sor_flow(), _session()
    with pytest.raises(ValueError, match="VALIDATION_POOL_MARKET_IDENTITY_MISMATCH"):
        flow._validation_pool_for_session(
            session, _pool(session, [_row("20261023090000", 100)],
                           source=source, identity=identity), target_count=5000,
        )


def test_v5_display_cannot_prepend_krx_or_replace_integrated_ohlcv():
    integrated = [_row("20261023080000", 100), _row("20261023090000", 110)]
    krx = [_row("20261022090000", 50), _row("20261023090000", 999)]
    assert _integrated_display_with_krx_prefix(krx, integrated, 5000) is None
    assert _integrated_display_with_krx_prefix(
        integrated, [_row("20261023090000", 999)], 5000,
    ) is None
    assert _integrated_display_with_krx_prefix(
        integrated, integrated, 5000,
    ) == integrated


@pytest.fixture
def chart_case():
    yield from baseline.case.__wrapped__()


def test_5000_bar_chart_checkbox_filters_time_without_changing_source(chart_case):
    window = baseline.window_for(chart_case)
    integrated = baseline.candles(5000)
    pre_index = next(i for i, row in enumerate(integrated)
                     if row["time"].endswith("090000"))
    integrated[pre_index] = {
        **integrated[pre_index],
        "time": integrated[pre_index]["time"][:8] + "083000",
    }
    window.set_historical_candle_pool(integrated, chart_candle_count=5000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")

    assert window._calculation_candles == integrated
    assert window._candles == integrated
    window.validation_regular_market_only_check.setChecked(True)
    assert window._calculation_candles == integrated
    assert window._candles == _krx_regular_session_candles(integrated)
    window.validation_regular_market_only_check.setChecked(False)
    assert window._calculation_candles == integrated
    assert window._candles == integrated
    assert len(window._candles) == 5000


def test_verified_live_m1_forming_candle_is_displayed_as_well_as_calculated(frozen_sor_plan):
    from gui_market_data_host import MarketDataHost
    from tests.test_sor_core_m1_input import NOW, _forming_host
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _registered_m1_calculation_case,
    )

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    market = _forming_host()
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    market._sor_m1_required_by_stock = {"005930": 5}
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: NOW + timedelta(seconds=30)

    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    view = flow._sor_m1_calculation_view(session, pool, selected, frozen)

    assert view["market_source"] == "INTEGRATED"
    assert view["market_data_identity"] == "005930_AL"
    assert view["candles"][-1]["time"] == "20261020090500"
    assert view["display_candles"] == view["candles"][-5:]
    assert view["display_candles"][-1]["close"] == 111
    assert pool["candles"][-1]["time"] == "20261020090400"


def test_verified_live_m1_preserves_detached_pre_and_latest_forming_row(frozen_sor_plan):
    from gui_market_data_host import MarketDataHost
    from tests.test_sor_core_m1_input import NOW, _forming_host
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _registered_m1_calculation_case,
    )

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    pre = _row("20261020083000", 80)
    flow._integrated_display_sidecars[flow._integrated_display_sidecar_key(session)] = (
        _proved_pre(session, [pre], as_of="2026-10-20T08:45:00+09:00")
    )
    market = _forming_host()
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    market._sor_m1_required_by_stock = {"005930": 5}
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: NOW + timedelta(seconds=30)

    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    view = flow._sor_m1_calculation_view(session, pool, selected, frozen)

    assert pre in view["candles"]
    assert view["candles"][-1]["time"] == "20261020090500"
    assert view["candles"][-1]["close"] == 111
    assert view["snapshot"].market_source == "VALIDATION_COMPOSITE"
    assert view["snapshot"].request_as_of != "2026-10-20T08:45:00+09:00"
    assert pool["candles"][-1]["time"] == "20261020090400"


@pytest.mark.parametrize("eligibility,error", [
    (None, "NXT_ELIGIBILITY_NOT_VERIFIED"),
    (False, "SOR_SOURCE_UNAVAILABLE"),
])
def test_unselected_or_unconfirmed_krx_stock_cannot_adopt_sor_pool(eligibility, error):
    flow, session = _flow(), _session()
    if eligibility is not None:
        flow._validation_source_by_code[session.request.stock.code] = eligibility
    pool = _pool(session, [_row("20261023090000", 100)])
    original = deepcopy(pool)
    with pytest.raises(ValueError, match=error):
        flow._validation_pool_for_session(session, pool)
    assert pool == original


@pytest.mark.parametrize("plan_state", ["missing", "KRX"])
def test_live_m1_rejects_missing_or_krx_source_plan(frozen_sor_plan, tmp_path, monkeypatch, plan_state):
    import gui_market_data_host as host_module
    from stock_market_source_plan import initialize_stock_market_source_plan
    from tests.test_sor_core_m1_input import NOW, _forming_host
    from tests.test_indicator_follow_signal_validation_v5_al_single_source import (
        _registered_m1_calculation_case,
    )

    flow, session, pool, _market, _ = _registered_m1_calculation_case()
    market = _forming_host()
    market.project_broker_sor_m1_calculation = lambda **kwargs: (
        host_module.MarketDataHost.project_broker_sor_m1_calculation(market, **kwargs)
    )
    market._sor_m1_required_by_stock = {"005930": 5}
    flow.parent().existing_market_data_host = lambda: market
    flow._now_factory = lambda: NOW + timedelta(seconds=30)
    selected = flow._validation_pool_for_session(session, pool, target_count=5)
    frozen = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    assert flow._sor_m1_calculation_view(session, pool, selected, frozen)["candles"][-1]["close"] == 111

    # Keep the same valid receipt; only remove/change the selected plan.
    root = tmp_path / plan_state
    if plan_state == "KRX":
        initialize_stock_market_source_plan(
            root, "005930", "Test", trading_day=NOW.date(), decided_at=NOW,
            nxt_available=False,
        )
    monkeypatch.setattr(host_module, "PROJECT_ROOT", root)
    blocked = flow._sor_m1_calculation_snapshot(session, pool, live_forming=True)
    with pytest.raises(ValueError, match="SOR_M1_BLOCKED:MARKET_SOURCE_PLAN_UNAVAILABLE"):
        flow._sor_m1_calculation_view(session, pool, selected, blocked)
    assert pool["candles"][-1]["time"] == "20261020090400"
