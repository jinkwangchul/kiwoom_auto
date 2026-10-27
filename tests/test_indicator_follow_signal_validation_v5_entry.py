from __future__ import annotations

import builtins
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QWidget

import gui_indicator_follow_routine_settings_dialog as dialog_module
import gui_indicator_follow_validation_stock_picker as picker_module
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
)
from tests.qt_test_support import ensure_qapplication


class _RequestDialog(QObject):
    signal_validation_requested = pyqtSignal(object)
    signal_validation_v5_requested = pyqtSignal(object)


def test_v5_picker_uses_flow_broker_for_voluntary_search_and_top100():
    from gui_indicator_follow_signal_validation_v5_flow import (
        IndicatorFollowSignalValidationV5Flow,
    )
    from gui_indicator_follow_validation_host import IndicatorFollowValidationHost

    class FakeBroker:
        def __init__(self):
            self.market_requests = []
            self.ranking_requests = []

        def request_initial_market_snapshot(self, codes, *, callback=None):
            self.market_requests.append((tuple(codes), callback))
            return {"ok": True}

        def request_stock_ranking_snapshot(self, source, *, callback=None):
            self.ranking_requests.append((source, callback))
            return {"ok": True}

    ensure_qapplication()
    broker = FakeBroker()
    window = QWidget()
    snapshot = StockLibraryLoadSnapshot(
        STOCK_LIBRARY_READY,
        STOCK_LIBRARY_RUNTIME_SOURCE,
        ({"code": "005930", "name": "Samsung", "market": "KOSPI"},),
    )
    stores = dict(
        recent_stock_store=SimpleNamespace(recent_stocks=(), startup_evicted_codes=()),
        preference_store=object(),
        historical_cache=object(),
    )
    flow = IndicatorFollowSignalValidationV5Flow(broker, **stores)
    with patch.object(picker_module, "load_stock_library_snapshot", return_value=snapshot):
        picker = flow.host.create_stock_picker(window)
    central_patch = patch.object(
        sys.modules[IndicatorFollowSignalValidationV5Flow.__module__],
        "read_base_stocks", return_value=[],
    )
    central_patch.start()
    try:
        assert picker.parent() is window
        assert picker._market_snapshot_api is broker
        assert broker.market_requests == []
        assert broker.ranking_requests == []

        picker.search_input.setText("005930")
        picker.btn_search.click()
        assert len(broker.market_requests) == 1
        assert broker.market_requests[0][0] == ("005930",)
        assert broker.ranking_requests == []
        broker.market_requests[0][1]({
            "ok": True,
            "rows": [{
                "stock_code": "005930",
                "current_price": 75000,
                "change_rate": 1.25,
                "execution_strength": 117.2,
                "previous_day_volume_rate": 12.43,
                "cumulative_trading_value": 287735,
                "cumulative_volume": 1523650,
                "market_capitalization": 4321000,
            }],
        })
        assert picker.result_table.rowCount() == 1
        assert [
            picker.result_table.item(0, column).text()
            for column in (
                picker.CURRENT_PRICE_COLUMN,
                picker.CHANGE_RATE_COLUMN,
                picker.EXECUTION_STRENGTH_COLUMN,
                picker.PREVIOUS_DAY_VOLUME_RATE_COLUMN,
                picker.TRADING_VALUE_COLUMN,
                picker.VOLUME_COLUMN,
                picker.MARKET_CAP_COLUMN,
            )
        ] == [
            "75,000", "+1.25%", "117.20", "+12.43%",
            "2,877억", "1,523,650주", "4,321,000억",
        ]

        picker.ranking_buttons["VOLUME_TOP"].click()
        assert len(broker.ranking_requests) == 1
        assert broker.ranking_requests[0][0] == "VOLUME_TOP"
        broker.ranking_requests[0][1]({
            "ok": True,
            "rows": [{
                "stock_code": "005930",
                "stock_name": "Samsung",
                "cumulative_volume": 999999,
            }],
        })
        assert picker.result_table.rowCount() == 1
        assert picker.result_table.item(0, picker.CODE_COLUMN).text() == "005930"
        assert picker.result_table.item(0, picker.VOLUME_COLUMN).text() == "999,999주"
        assert len(broker.market_requests) == 2
        assert broker.market_requests[1][0] == ("005930",)
        broker.market_requests[1][1]({
            "ok": True,
            "rows": [{"stock_code": "005930", "current_price": 75000}],
        })
        assert picker.result_table.item(0, picker.CURRENT_PRICE_COLUMN).text() == "75,000"

        custom_host = IndicatorFollowValidationHost()
        custom_flow = IndicatorFollowSignalValidationV5Flow(
            broker, host=custom_host, **stores
        )
        assert custom_flow.host is custom_host
    finally:
        central_patch.stop()
        picker.close()
        window.close()


def test_v5_picker_registration_status_uses_one_fresh_local_snapshot_per_result(tmp_path):
    import gui_indicator_follow_signal_validation_v5_flow as v5_module

    class FakeBroker:
        def __init__(self):
            self.market_requests = []
            self.ranking_requests = []

        def request_initial_market_snapshot(self, codes, *, callback=None):
            self.market_requests.append((tuple(codes), callback))

        def request_stock_ranking_snapshot(self, source, *, callback=None):
            self.ranking_requests.append((source, callback))

    codes = ("000001", "000002", "000003", "000004", "000005", "000006")
    records = tuple(
        {"code": code, "name": f"Stock {index}", "market": "KOSPI"}
        for index, code in enumerate(codes, 1)
    )
    central = [
        {"code": code, "name": record["name"],
         "stock_path": f"stocks/{code}",
         "assigned_routine_instance_id": assignment}
        for code, record, assignment in zip(
            codes[1:], records[1:], ("", "routine-a", "missing-id", "routine-a", "")
        )
    ]
    for stock in central[:-1]:
        directory = tmp_path / stock["stock_path"]
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(
            json.dumps({"status": "STOPPED", "review_required": stock["code"] == "000005"}),
            encoding="utf-8",
        )
    original_states = {
        stock["code"]: (tmp_path / stock["stock_path"] / "state.json").read_bytes()
        for stock in central[:-1]
    }
    snapshot = StockLibraryLoadSnapshot(
        STOCK_LIBRARY_READY, STOCK_LIBRARY_RUNTIME_SOURCE, records
    )
    broker = FakeBroker()
    stores = dict(
        recent_stock_store=SimpleNamespace(recent_stocks=(), startup_evicted_codes=()),
        preference_store=object(), historical_cache=object(),
    )
    ensure_qapplication()
    window = QWidget()
    with (
        patch.object(picker_module, "load_stock_library_snapshot", return_value=snapshot),
        patch.object(v5_module, "PROJECT_ROOT", tmp_path),
        patch.object(v5_module, "read_base_stocks", return_value=central) as central_reader,
        patch.object(v5_module, "load_persisted_routine_instances", return_value=[
            SimpleNamespace(instance_id="routine-a", display_name="Routine Alpha")
        ]) as instance_reader,
    ):
        default_picker = picker_module.IndicatorFollowValidationStockPicker(
            snapshot_loader=lambda _root: snapshot
        )
        flow = v5_module.IndicatorFollowSignalValidationV5Flow(broker, **stores)
        picker = flow.host.create_stock_picker(window)
        try:
            default_picker.search_input.setText("Stock")
            default_picker.search_stocks()
            assert default_picker.result_table.item(
                0, default_picker.REGISTRATION_STATUS_COLUMN
            ).text() == "-"

            picker.search_input.setText("Stock")
            picker.search_stocks()
            expected = {
                "000001": "등록대기", "000002": "등록대기",
                "000003": "Routine Alpha", "000004": "missing-id",
                "000005": "검토관리", "000006": "검토관리",
            }

            def displayed_statuses():
                return {
                    picker.result_table.item(row, picker.CODE_COLUMN).text():
                    picker.result_table.item(row, picker.REGISTRATION_STATUS_COLUMN).text()
                    for row in range(picker.result_table.rowCount())
                }

            assert displayed_statuses() == expected
            assert central_reader.call_count == 1
            assert instance_reader.call_count == 1
            assert len(broker.market_requests) == 1

            picker.ranking_buttons["VOLUME_TOP"].click()
            assert len(broker.ranking_requests) == 1
            broker.ranking_requests[0][1]({
                "ok": True, "rows": [
                    {"stock_code": record["code"], "stock_name": record["name"]}
                    for record in records
                ],
            })
            assert displayed_statuses() == expected
            assert central_reader.call_count == 2
            assert instance_reader.call_count == 2
            assert len(broker.market_requests) == 2

            with patch.object(
                v5_module, "load_persisted_routine_instances",
                side_effect=OSError("registry unavailable"),
            ):
                picker.search_stocks()
            assert displayed_statuses()["000003"] == "-"
            assert displayed_statuses()["000004"] == "-"
            assert displayed_statuses()["000002"] == expected["000002"]
            assert displayed_statuses()["000005"] == expected["000005"]

            read_state = v5_module.read_json_dict
            def unavailable_state(path):
                if path.parent.name == "000003":
                    raise OSError("state unavailable")
                return read_state(path)
            with patch.object(v5_module, "read_json_dict", side_effect=unavailable_state):
                picker.search_stocks()
            assert displayed_statuses()["000003"] == "-"
            assert displayed_statuses()["000004"] == expected["000004"]

            with patch.object(v5_module, "read_base_stocks", side_effect=OSError("central unavailable")):
                picker.search_stocks()
            assert set(displayed_statuses().values()) == {"-"}
            assert {
                stock["code"]: (tmp_path / stock["stock_path"] / "state.json").read_bytes()
                for stock in central[:-1]
            } == original_states
            assert not (tmp_path / "stocks" / "000001").exists()
        finally:
            picker.close()
            default_picker.close()
            window.close()


def test_v5_modules_are_standalone_and_flow_uses_v5_window():
    from gui_indicator_follow_signal_validation_window import (
        IndicatorFollowSignalValidationWindow,
    )
    from gui_indicator_follow_signal_validation_v5_flow import (
        IndicatorFollowSignalValidationV5Flow,
    )
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    assert IndicatorFollowSignalValidationV5Window is not IndicatorFollowSignalValidationWindow
    assert importlib.util.find_spec("gui_indicator_follow_signal_validation_v4_flow") is None
    assert importlib.util.find_spec("gui_indicator_follow_signal_validation_v4_window") is None
    assert (
        IndicatorFollowSignalValidationV5Flow.__init__.__kwdefaults__["window_factory"]
        is IndicatorFollowSignalValidationV5Window
    )


def test_v5_and_v2_binders_keep_separate_owner_state():
    from gui_indicator_follow_signal_validation_flow import (
        bind_indicator_follow_signal_validation_flow,
    )
    from gui_indicator_follow_signal_validation_v5_flow import (
        bind_indicator_follow_signal_validation_v5_flow,
    )

    ensure_qapplication()
    owner = QObject()
    owner.kiwoom_api = None
    request_dialog = _RequestDialog()

    v2_flow = bind_indicator_follow_signal_validation_flow(owner, request_dialog)
    v5_flow = bind_indicator_follow_signal_validation_v5_flow(owner, request_dialog)

    assert v2_flow is owner._indicator_follow_signal_validation_flow
    assert v5_flow is owner._indicator_follow_signal_validation_v5_flow
    assert v5_flow is not v2_flow


def test_settings_dialog_exposes_v5_without_v4_entry_and_preserves_seed():
    ensure_qapplication()
    project_root = Path(__file__).resolve().parents[1]
    routine_path = project_root / "routines" / "지표추종매매"

    with (
        patch.object(dialog_module.QTimer, "singleShot"),
        patch.object(dialog_module, "persistent_feature_owner", return_value=None),
    ):
        dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
            rules_path=routine_path / "rules.json",
            routine_path=routine_path,
            routine_name="지표추종매매",
            definition_id="indicator_follow",
            settings_mode="registration",
        )

    try:
        assert not hasattr(dialog, "signal_validation_v3_button")
        assert not hasattr(dialog, "signal_validation_v3_requested")
        assert "gui_indicator_follow_signal_validation_v3_window" not in sys.modules
        assert importlib.util.find_spec("gui_indicator_follow_signal_validation_v3_window") is None
        assert not hasattr(dialog, "signal_validation_v4_button")
        assert not hasattr(dialog, "signal_validation_v4_requested")
        assert not hasattr(dialog, "_handle_signal_validation_v4_clicked")
        assert dialog.signal_validation_v5_button.text() == "검증차트5"

        v5_payloads = []
        dialog.signal_validation_v5_requested.connect(v5_payloads.append)

        v5_seed = dialog._handle_signal_validation_v5_clicked()

        assert v5_seed is not None
        assert v5_payloads == [v5_seed]
        assert v5_seed.to_ui_state()["basic"]["buy_signal_expr_line"] == "A"
        assert v5_seed.to_ui_state()["basic"]["sell_signal_expr_line"] == "A"
    finally:
        dialog.close()


def test_missing_optional_v5_disables_only_v5_entry():
    ensure_qapplication()
    project_root = Path(__file__).resolve().parents[1]
    routine_path = project_root / "routines" / "지표추종매매"
    owner = QObject()
    owner.kiwoom_api = None
    real_import = builtins.__import__

    def import_without_v5(name, *args, **kwargs):
        if name == "gui_indicator_follow_signal_validation_v4_flow":
            raise AssertionError("retired V4 flow was imported")
        if name == "gui_indicator_follow_signal_validation_v5_flow":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    with (
        patch.object(dialog_module.QTimer, "singleShot"),
        patch.object(dialog_module, "persistent_feature_owner", return_value=owner),
        patch.object(builtins, "__import__", side_effect=import_without_v5),
    ):
        dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
            rules_path=routine_path / "rules.json",
            routine_path=routine_path,
            routine_name="지표추종매매",
            parent=owner,
            definition_id="indicator_follow",
            settings_mode="registration",
        )

    try:
        assert not hasattr(dialog, "signal_validation_v4_button")
        assert not dialog.signal_validation_v5_button.isEnabled()
        assert not hasattr(owner, "_indicator_follow_signal_validation_v4_flow")
        assert not hasattr(owner, "_indicator_follow_signal_validation_v5_flow")
    finally:
        dialog.close()
