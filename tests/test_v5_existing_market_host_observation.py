"""V5 source diagnostics read only an already owned market host."""

import json
from types import SimpleNamespace

from PyQt5.QtCore import QObject

from gui_indicator_follow_signal_validation_v5_flow import (
    IndicatorFollowSignalValidationV5Flow,
)
from gui_windows import MainWindow


def test_owner_lookup_never_constructs_operation_host():
    def forbidden_creation():
        raise AssertionError("operation host construction attempted")

    owner = SimpleNamespace(main_monitoring_auto_trade_operation_host=forbidden_creation)
    assert MainWindow.existing_market_data_host(owner) is None

    market = object()
    owner._main_monitoring_auto_trade_operation_host = SimpleNamespace(
        market_data_host=lambda: market
    )
    assert MainWindow.existing_market_data_host(owner) is market


def test_v5_observes_separate_source_availability_and_quality_without_registration():
    owner = QObject()
    market = SimpleNamespace(
        high_resolution_market_state=lambda code: SimpleNamespace(data_quality="UNCERTAIN"),
        nxt_display_live_price_state=lambda code: SimpleNamespace(data_quality="NORMAL"),
        sync_targets=lambda *_: (_ for _ in ()).throw(AssertionError("registration attempted")),
    )
    owner.existing_market_data_host = lambda: market
    flow = IndicatorFollowSignalValidationV5Flow(None, parent=owner)

    assert flow._market_source_observation("005930") == {
        "host_available": True,
        "krx_available": True,
        "krx_quality": "UNCERTAIN",
        "nxt_available": True,
        "nxt_quality": "NORMAL",
    }

    market.nxt_display_live_price_state = lambda code: None
    assert flow._market_source_observation("005930")["nxt_available"] is False

    owner.existing_market_data_host = lambda: None
    assert flow._market_source_observation("005930") == {
        "host_available": False,
        "krx_available": False,
        "krx_quality": None,
        "nxt_available": False,
        "nxt_quality": None,
    }


def test_v5_sor_registration_observation_keeps_sources_separate_and_sessions_private():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("broker registration attempted")

    owner = QObject()
    api = SimpleNamespace(
        realtime_shadow_registration_snapshot=lambda: SimpleNamespace(
            active=True,
            connection_epoch=7,
            login_session_id="SECRET-KRX-SESSION",
            target_stock_codes=("005930",),
            shadow_target_stock_codes=("005930",),
            fid_list=(10, 15),
            screen_batches=(SimpleNamespace(screen_no="5000", stock_codes=("005930",)),),
        ),
        nxt_display_registration_snapshot=lambda: SimpleNamespace(
            active=True,
            connection_epoch=7,
            login_session_id="SECRET-NXT-SESSION",
            canonical_stock_codes=("005930",),
            broker_code_identities=("005930_NX",),
            fid_list=(20, 21),
            screen_batches=(SimpleNamespace(screen_no="5001", stock_codes=("005930_NX",)),),
        ),
        _nxt_display_receive_sequence=9,
        sync_nxt_display_registration=forbidden,
        sync_realtime_shadow_targets=forbidden,
    )
    market = SimpleNamespace(
        kiwoom_api=api,
        high_resolution_market_data_snapshot=lambda: SimpleNamespace(
            received_tick_count=12,
            processed_tick_count=11,
            overflow_count=1,
            current_queue_depth=0,
        ),
        high_resolution_market_state=lambda _code: SimpleNamespace(
            received_tick_count=5, processed_tick_count=4,
        ),
        nxt_display_live_price_state=lambda _code: SimpleNamespace(receive_sequence=8),
        _nxt_display_tick_queue=(),
        sync_targets=forbidden,
    )
    owner.existing_market_data_host = lambda: market
    flow = IndicatorFollowSignalValidationV5Flow(None, parent=owner)

    snapshot = flow._sor_registration_observation("005930")
    assert snapshot["krx_registration"]["target_stock_codes"] == ("005930",)
    assert snapshot["krx_registration"]["screen_batches"] == (("5000", ("005930",)),)
    assert snapshot["nxt_registration"]["broker_code_identities"] == ("005930_NX",)
    assert snapshot["nxt_registration"]["screen_batches"] == (("5001", ("005930_NX",)),)
    assert snapshot["krx_ticks"] == {
        "received": 12, "processed": 11, "overflow": 1, "queue_depth": 0,
        "stock_received": 5, "stock_processed": 4,
    }
    assert snapshot["nxt_ticks"] == {
        "api_receive_sequence": 9, "stock_last_receive_sequence": 8,
        "queue_depth": 0,
    }
    assert snapshot["krx_registration"]["session_sha256"]
    assert snapshot["nxt_registration"]["session_sha256"]
    serialized = json.dumps(snapshot)
    assert "SECRET-" not in serialized


def test_v5_sor_registration_observation_does_not_create_market_host():
    owner = QObject()
    owner.existing_market_data_host = lambda: None
    flow = IndicatorFollowSignalValidationV5Flow(None, parent=owner)
    assert flow._sor_registration_observation("005930") == {"host_available": False}
