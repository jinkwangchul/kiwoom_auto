"""Detached settings evidence: no broker, authentication, or live GUI."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from indicator_follow_signal_validation_projection import IndicatorFollowSignalValidationSeed
from indicator_follow_signal_validation_visualization import ValidationFilterDescriptor
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot, ValidationStockRef,
)

MODULE = "indicator_follow_signal_validation_v5_settings_diagnostic"


@pytest.fixture
def diagnostic(monkeypatch, tmp_path):
    assert importlib.util.find_spec(MODULE) is not None, "V5 settings exporter is missing"
    module = importlib.import_module(MODULE)
    monkeypatch.setattr(module, "OUTPUT_DIRECTORY", tmp_path / "diagnostics")
    monkeypatch.setenv("KIWOOM_V5_DIAG_SETTINGS_EXPORT", "1")
    return module


def sample():
    rules = {
        "bar": {"bar_minutes": 3},
        "indicators": {"moving_averages": [60], "rsi": {"period": 14},
                       "macd": {"fast": 12, "slow": 26, "signal": 9}},
        "buy": {"expression": "A AND NOT B"},
        "sell": {"expression": "A", "ocr": {"threshold": 0}},
    }
    snapshot = ValidationSettingsSnapshot(rules)
    ui = {"basic": {"buy_signal_expr_line": "A AND NOT B", "sell_signal_expr_line": "A"}}
    descriptor = ValidationFilterDescriptor(
        "MA:600", "MOVING_AVERAGE", "MA600", "PRICE", ("BUY",),
        ("MA600",), '{"period":600}', (),
    )
    return SimpleNamespace(
        stock=ValidationStockRef("005930", "Samsung"),
        _signal_validation_seed=IndicatorFollowSignalValidationSeed(snapshot, ui),
        _result_ui_state=deepcopy(ui), _result_settings_snapshot=snapshot,
        _normal_base_ready=True, _normal_base_timeframe_key="M3",
        _replay_snapshot=None, _chart_results_suspended=False,
        _visualization_descriptors=(descriptor,), _visualization_data_error="",
        _visualization_current_key=(snapshot.rules_hash, 1, "ui-fingerprint"),
        _visualization_projection_active=None, _visualization_projection_pending=None,
        _visualization_data_epoch=1, _visualization_projection_generation=2,
        _not_filter_identities={("BUY", "MA:600")}, _inner_not_filter_identities=set(),
        _calculation_candles=[{"time": "20261015150000"}], _candles=[{}],
        canvas=SimpleNamespace(), _validation_range=None,
    )


def read_export(diagnostic, window, **kwargs):
    outcome = diagnostic.export_settings_snapshot(window, "test", **kwargs)
    assert outcome["status"] == "EXPORTED", outcome
    return json.loads(outcome["path"].read_text(encoding="utf-8"))


def test_off_does_not_read_objects_or_create_directory(diagnostic, monkeypatch):
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("OFF must not inspect state")
    monkeypatch.delenv("KIWOOM_V5_DIAG_SETTINGS_EXPORT")
    assert diagnostic.export_settings_snapshot(Unreadable(), "off")["status"] == "DISABLED"
    assert not diagnostic.OUTPUT_DIRECTORY.exists()


def test_export_separates_result_ma60_from_visual_ma600_without_mutation(diagnostic):
    window = sample()
    before = deepcopy(vars(window))
    result = read_export(diagnostic, window, request_settings=window._result_settings_snapshot)
    assert result["calculation_settings"]["result"]["confirmed"] is True
    assert result["calculation_settings"]["result"]["rules"]["indicators"]["moving_averages"] == [60]
    assert result["visualization_settings"]["descriptors"][0]["parameters"]["period"] == 600
    assert result["comparisons"]["request_vs_result"] == "MATCH"
    assert result["ui_state"]["current_input_confirmed"] is False
    assert result["ui_state"]["value"] == before["_result_ui_state"]
    assert window._result_settings_snapshot.canonical_json == before["_result_settings_snapshot"].canonical_json
    assert window._visualization_descriptors == before["_visualization_descriptors"]
    assert window._result_ui_state == before["_result_ui_state"]
    assert set(vars(window)) == set(before)


@pytest.mark.parametrize("has_snapshot,ready", [(False, False), (True, False)])
def test_unapplied_or_missing_result_is_never_confirmed(diagnostic, has_snapshot, ready):
    window = sample()
    window._normal_base_ready = ready
    if not has_snapshot:
        window._result_settings_snapshot = None
    result = read_export(diagnostic, window)
    assert result["status"] == "SNAPSHOT_UNAVAILABLE"
    assert result["calculation_settings"]["result"]["confirmed"] is False


def test_projection_request_rules_are_distinct_from_applied_descriptors(diagnostic):
    window = sample()
    rules = window._result_settings_snapshot.to_dict()
    rules["indicators"]["moving_averages"] = [600]
    request = {"rules": rules, "descriptors": window._visualization_descriptors,
               "ui_state": window._result_ui_state, "key": window._visualization_current_key,
               "generation": 2, "data_epoch": 1}
    result = read_export(diagnostic, window, projection_request=request)
    submitted = result["visualization_settings"]["submitted_request"]
    assert submitted["rules"]["indicators"]["moving_averages"] == [600]
    assert submitted["application_confirmed"] is False
    assert result["comparisons"]["result_vs_submitted_projection"] == "DIFFERENT"
    assert result["calculation_settings"]["result"]["rules"]["indicators"]["moving_averages"] == [60]


def test_range_request_b_is_not_mislabeled_as_base_result_a(diagnostic):
    window = sample()
    rules_b = window._result_settings_snapshot.to_dict()
    rules_b["indicators"]["moving_averages"] = [600]
    window._range_replay_settings_snapshot = ValidationSettingsSnapshot(rules_b)
    window._range_replay_entries_available = True
    window._validation_range = (10, 20)
    result = read_export(diagnostic, window, request_settings=window._range_replay_settings_snapshot)
    assert result["comparisons"]["request_vs_result"] == "DIFFERENT"
    assert result["comparisons"]["request_vs_range_result"] == "MATCH"
    assert result["calculation_settings"]["range_result"]["confirmed"] is True


def test_sensitive_fields_are_redacted_at_every_depth(diagnostic):
    window = sample()
    rules = window._result_settings_snapshot.to_dict()
    rules["account_number"] = "SECRET_ACCOUNT"
    rules["buy"]["api_token"] = "SECRET_TOKEN"
    window._result_settings_snapshot = ValidationSettingsSnapshot(rules)
    window._result_ui_state["password"] = "SECRET_PASSWORD"
    result = read_export(diagnostic, window)
    encoded = json.dumps(result)
    assert not any(secret in encoded for secret in ("SECRET_ACCOUNT", "SECRET_TOKEN", "SECRET_PASSWORD"))
    assert result["calculation_settings"]["result"]["rules_hash"] == window._result_settings_snapshot.rules_hash
    assert result["calculation_settings"]["result"]["rules"]["account_number"] == "[REDACTED]"


def test_repeated_export_never_overwrites_existing_file(diagnostic):
    window = sample()
    first = diagnostic.export_settings_snapshot(window, "first")
    original = first["path"].read_bytes()
    second = diagnostic.export_settings_snapshot(window, "second")
    assert first["path"] != second["path"]
    assert first["path"].read_bytes() == original


def test_write_failure_does_not_escape_or_mutate_chart(diagnostic):
    diagnostic.OUTPUT_DIRECTORY.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.OUTPUT_DIRECTORY.write_text("existing", encoding="utf-8")
    window = sample()
    canvas = window.canvas
    result = diagnostic.export_settings_snapshot(window, "failure")
    assert result["status"] == "EXPORT_FAILED"
    assert window.canvas is canvas
    assert window._normal_base_ready is True
    assert diagnostic.OUTPUT_DIRECTORY.read_text(encoding="utf-8") == "existing"


@pytest.fixture
def actual_window(monkeypatch):
    from unittest.mock import patch
    import gui_indicator_follow_signal_validation_v5_window as wm
    from tests import test_indicator_follow_signal_validation_v2 as legacy
    from tests.qt_test_support import dispose_qt_widget
    cls = legacy.IndicatorFollowSignalValidationV2Test
    cls.setUpClass()
    seed = cls(methodName="runTest")._seed()
    with patch.object(wm.QTimer, "singleShot"):
        window = wm.IndicatorFollowSignalValidationV5Window(cls.stock, seed)
    # Isolate external scheduling, not the diagnostic/application methods under test.
    monkeypatch.setattr(window, "_refresh_filter_display_projection", lambda: None)
    rows = [{"time": f"20261015150{i}00", "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": 10} for i in range(6)]
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    presentation = ((), wm.ValidationIndicatorSeriesCache(len(rows), ()))
    window.install_normal_base(seed.settings_snapshot, "M3", presentation=presentation)
    try:
        yield window
    finally:
        dispose_qt_widget(window, close=True)


def test_actual_projection_application_exports_without_canvas_or_range_change(diagnostic, actual_window):
    window = actual_window
    canvas = window.canvas
    window._validation_range = (1, 3)
    emitted = []
    window.historical_extension_requested.connect(lambda: emitted.append(True))
    window._apply_visualization_projection(
        window._visualization_descriptors, window._visualization_cache, {},
        key=window._visualization_current_key, ocr_turn_ui_state=window._result_ui_state,
    )
    paths = list(diagnostic.OUTPUT_DIRECTORY.glob("*.json"))
    assert paths, "Actual V5 projection application must export diagnostic evidence"
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    assert payload["event"] == "visualization_applied"
    assert payload["visualization_settings"]["application_confirmed"] is True
    assert payload["module_paths"]["gui_indicator_follow_signal_validation_v5_window"].endswith("_v5_window.py")
    assert window.canvas is canvas
    assert window._validation_range == (1, 3)
    assert emitted == []


def test_actual_normal_base_flow_exports_exact_request(diagnostic, actual_window):
    import gui_indicator_follow_signal_validation_v5_flow as fm
    from routines.지표추종매매.routine_validation_contract import ValidationRequest
    from routines.지표추종매매.routine_validation_session import ValidationSession
    window = actual_window
    flow = fm.IndicatorFollowSignalValidationV5Flow(
        object(), recent_stock_store=SimpleNamespace(recent_stocks=()),
        preference_store=SimpleNamespace(), historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_: {"ready": False},
    )
    session = ValidationSession(ValidationRequest(window.stock, window._result_settings_snapshot, 3))
    pool = {"stock": window.stock, "candles": window._calculation_candles,
            "timeframe_key": "M3", "timeframe_minutes": 3,
            "market_source": "KRX", "market_data_identity": "005930",
            "requested_count": len(window._calculation_candles)}
    assert flow._apply_pool_to_window(
        window, session, pool, evaluation_count=6, signal_entries=None,
        prepared_view_pool=pool,
        normal_base_presentation=(window._visualization_descriptors, window._visualization_cache),
    ) is True
    payloads = [json.loads(path.read_text(encoding="utf-8"))
                for path in diagnostic.OUTPUT_DIRECTORY.glob("*.json")]
    applied = [item for item in payloads if item["event"] == "calculation_applied"]
    assert applied, "Successful prepared V5 application must export its actual request"
    assert applied[-1]["calculation_settings"]["request"]["rules_hash"] == session.request.settings_snapshot.rules_hash
    assert applied[-1]["comparisons"]["request_vs_result"] == "MATCH"


def test_actual_projection_submission_and_acceptance_correlate(diagnostic, actual_window, monkeypatch):
    import gui_indicator_follow_signal_validation_v5_window as wm
    window = actual_window
    key = (window._result_settings_snapshot.rules_hash, window._visualization_data_epoch, "test")
    request = window._visualization_projection_request(
        window._visualization_projection_generation, key,
        window._result_settings_snapshot.to_dict(), window._visualization_descriptors,
        window._result_ui_state,
    )
    tasks = []
    # Control only dispatch timing; execute the real projection worker and receiver.
    monkeypatch.setattr(wm, "Thread", lambda **kw: SimpleNamespace(start=lambda: tasks.append(kw["target"])))
    window._start_visualization_projection(request)
    assert len(tasks) == 1
    tasks[0]()
    payloads = [json.loads(path.read_text(encoding="utf-8"))
                for path in diagnostic.OUTPUT_DIRECTORY.glob("*.json")]
    submitted = next(p for p in payloads if p["event"] == "visualization_submitted")
    applied = next(p for p in payloads if p["event"] == "visualization_applied")
    request_evidence = submitted["visualization_settings"]["submitted_request"]
    assert request_evidence["projection_key_sha256"] == applied["visualization_settings"]["projection_key_sha256"]
    assert request_evidence["rules_sha256"] == window._result_settings_snapshot.rules_hash
    assert request_evidence["application_confirmed"] is False
    assert applied["visualization_settings"]["application_confirmed"] is True


def test_stale_projection_completion_never_exports_applied_evidence(diagnostic, actual_window):
    window = actual_window
    window._on_visualization_projection_completed({
        "token": object(), "generation": window._visualization_projection_generation - 1,
        "data_epoch": window._visualization_data_epoch,
        "cache": window._visualization_cache,
    })
    assert not list(diagnostic.OUTPUT_DIRECTORY.glob("*.json"))


@pytest.mark.parametrize("mode", ["off", "write_failure"])
def test_actual_apply_off_or_export_failure_preserves_chart(diagnostic, actual_window, monkeypatch, mode):
    window = actual_window
    if mode == "off":
        monkeypatch.delenv("KIWOOM_V5_DIAG_SETTINGS_EXPORT")
    else:
        diagnostic.OUTPUT_DIRECTORY.write_text("existing", encoding="utf-8")
    canvas, snapshot = window.canvas, window._result_settings_snapshot
    candles = deepcopy(window._calculation_candles)
    window._validation_range = (1, 3)
    window._apply_visualization_projection(
        window._visualization_descriptors, window._visualization_cache, {},
        ocr_turn_ui_state=window._result_ui_state,
    )
    assert window.canvas is canvas
    assert window._result_settings_snapshot is snapshot
    assert window._calculation_candles == candles
    assert window._validation_range == (1, 3)
    if mode == "off":
        assert not diagnostic.OUTPUT_DIRECTORY.exists()
    else:
        assert diagnostic.OUTPUT_DIRECTORY.read_text(encoding="utf-8") == "existing"


def test_actual_range_overlay_preserves_confirmed_normal_base_settings(diagnostic, actual_window):
    from routines.지표추종매매.routine_validation_replay import ValidationReplaySnapshot
    window = actual_window
    base = window._result_settings_snapshot
    range_rules = base.to_dict()
    range_rules["diagnostic_test_variant"] = "B"
    selected = ValidationSettingsSnapshot(range_rules)
    window._validation_range = (1, 3)
    replay = ValidationReplaySnapshot(
        stock=window.stock, timeframe_minutes=3, settings_hash=selected.rules_hash,
        historical_request_id="diagnostic-test", evaluated_start_index=1,
        evaluated_end_index=3, dropped_raw_rows_count=0,
        candles=window._calculation_candles, entries=[],
    )
    window.apply_range_replay_snapshot_async(replay, settings_snapshot=selected)
    assert window._range_replay_from_normal_base
    assert not window._normal_base_ready
    evidence = read_export(diagnostic, window, request_settings=selected)
    assert evidence["status"] == "SNAPSHOT_CAPTURED"
    assert evidence["calculation_settings"]["result"]["confirmed"] is True
    assert evidence["calculation_settings"]["range_result"]["confirmed"] is True
    assert evidence["comparisons"]["request_vs_result"] == "DIFFERENT"
    assert evidence["comparisons"]["request_vs_range_result"] == "MATCH"
    assert window._result_settings_snapshot is base
