"""Offline verified view handoff shared by V5 scan and normal base."""

from types import SimpleNamespace

import pytest

from gui_indicator_follow_signal_validation_v5_flow import (
    IndicatorFollowSignalValidationV5Flow,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef
from tests.test_indicator_follow_validation_v5_sor_tail_bridge import SHA, evidence, view


def _session():
    return SimpleNamespace(request=SimpleNamespace(
        stock=ValidationStockRef("005930", "Samsung"),
        timeframe_key="M5", timeframe_minutes=5,
    ))


def _flow(batch, identity):
    return SimpleNamespace(
        _verified_sor_tail_provider=lambda _session, _sha, _as_of: batch,
        _pool_computation_source_identity=lambda _pool: identity,
        _historical_rows_from_candles=lambda candles: [dict(row) for row in candles],
    )


def test_shared_view_uses_one_verified_tail_for_replay_and_normal_base():
    as_of, batch = evidence("M5")
    raw = view(batch, "M5")
    identity = ("source",) * 8 + (SHA,)
    flow = _flow(batch, identity)
    make_view = IndicatorFollowSignalValidationV5Flow._verified_sor_view_for_session
    replay = make_view(flow, _session(), raw, dict(raw), as_of, identity)
    normal = make_view(flow, _session(), raw, dict(raw), as_of, identity)
    assert replay["candles"][-1] == normal["candles"][-1]
    assert replay["display_candles"][-1] == normal["display_candles"][-1]
    assert replay["snapshot"].to_rows()[-1] == normal["snapshot"].to_rows()[-1]
    assert replay["sor_tail_calculation_complete"] is True
    assert raw["candles"][-1] != replay["candles"][-1]


def test_shared_view_rejects_source_change_and_missing_attestation():
    as_of, batch = evidence("M5")
    raw = view(batch, "M5")
    identity = ("source",) * 8 + (SHA,)
    make_view = IndicatorFollowSignalValidationV5Flow._verified_sor_view_for_session
    with pytest.raises(ValueError, match="SOURCE_CHANGED_AFTER_DISPATCH"):
        make_view(_flow(batch, ("changed",) * 8 + (SHA,)),
                  _session(), raw, dict(raw), as_of, identity)
    with pytest.raises(ValueError, match="SESSION_OR_ATTESTATION"):
        make_view(_flow(None, identity), _session(), raw, dict(raw), as_of, identity)
