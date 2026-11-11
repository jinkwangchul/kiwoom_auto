"""Focused V5 behavior baseline with all persistence dependencies isolated."""
from __future__ import annotations

from datetime import datetime, timedelta
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtCore import QRectF, QSignalBlocker
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest

import gui_indicator_follow_routine_settings_dialog as dialog_module
import gui_indicator_follow_signal_validation_v5_flow as flow_module
import gui_indicator_follow_signal_validation_v5_window as window_module
from tests.v5_validation_test_support import V5ValidationCase, _FakeHost
from tests import v5_validation_test_support as support


@pytest.fixture(autouse=True)
def diskless_baseline(monkeypatch):
    # Baseline windows need no disk cache, including during fixture cleanup.
    monkeypatch.setattr(support.tempfile, "TemporaryDirectory", lambda:
                        SimpleNamespace(name="unused-v5-baseline", cleanup=lambda: None))
    monkeypatch.setattr(support, "IndicatorFollowSignalValidationHistoricalCache",
                        lambda *_args, **_kwargs: SimpleNamespace())
    for module in (window_module, flow_module):
        monkeypatch.setattr(module, "trace_signal_validation_event", lambda *_a, **_k: None)
        monkeypatch.setattr(module, "signal_validation_trace_enabled", lambda: False)
    monkeypatch.setattr(flow_module, "_trace_v5_marking", lambda *_a, **_k: None)
    monkeypatch.setattr(flow_module, "_trace_sor_al_m1", lambda *_a, **_k: None)
    monkeypatch.setattr(flow_module, "_queue_v5_m3_evidence", lambda *_a, **_k: False)


@pytest.fixture
def case():
    cls = V5ValidationCase
    cls.setUpClass()
    instance = cls()
    instance.setUp()
    try:
        yield instance
    finally:
        instance.tearDown()


def window_for(case):
    with patch.object(dialog_module.QTimer, "singleShot"):
        window = window_module.IndicatorFollowSignalValidationV5Window(
            case.stock,
            case._seed(),
        )
    case.widgets.append(window)
    return window


@pytest.fixture
def timeframe_pending(case, monkeypatch, request):
    """Real window/flow/requester; held offline transport and recorded native M3 bars."""
    from stock_market_source_plan import _build

    path = Path(__file__).resolve().parents[1] / "runtime/signal_validation_v2_history_cache/000250_AL_3_5900.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    initial_count = getattr(request, "param", 5900)
    query = json.loads(Path(str(path) + ".market-source.json").read_text(
        encoding="utf-8"))["direct_selected_query"]
    stock = flow_module.ValidationStockRef("000250", "offline recorded M3")
    with patch.object(dialog_module.QTimer, "singleShot"):
        window = window_module.IndicatorFollowSignalValidationV5Window(stock, case._seed())
    case.widgets.append(window)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("3")
    settings = window._marking_current_settings()
    clock = [datetime.fromisoformat("2026-11-09T15:48:00+09:00")]
    plan = _build(stock.code, stock.name, clock[0].date(), clock[0].isoformat(), True)
    monkeypatch.setattr(flow_module, "read_stock_market_source_plan", lambda *_a: deepcopy(plan))
    monkeypatch.setattr(flow_module, "read_json_dict", lambda *_a: {})
    calls, published = [], []
    broker_session = SimpleNamespace(connected=True, connection_epoch=7,
                                     login_session_id="OFFLINE-TIMEFRAME-TEST")

    def dispatch(code, name, **options):
        started = dict(ok=True, code=code, rqname=f"offline-native-{len(calls)}",
            market_data_identity=code + "_AL", market_source="INTEGRATED", display_only=True,
            request_as_of=clock[0].isoformat(), started_at=clock[0].isoformat(),
            connection_epoch=7, login_session_id=broker_session.login_session_id)
        calls.append((started, options))
        broker._pending_tr[started["rqname"]] = {"count": options["count"]}
        return started

    broker = SimpleNamespace(request_minute_candles_display_only_integrated=dispatch,
                             _pending_tr={},
                             broker_session_snapshot=lambda: broker_session)
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        broker, host=_FakeHost(stock), recent_stock_store=support._MemoryRecentStockStore(),
        preference_store=SimpleNamespace(), historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_a: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False})
    flow._now_factory = lambda: clock[0]
    flow._operation_active_reader = lambda: False
    key = id(window)
    flow._open_windows[key] = window
    flow._request_generation[key] = 1
    flow._validation_source_by_code[stock.code] = True
    flow._history_targets[key] = min(5000, initial_count)
    session = flow_module.ValidationSession(flow_module.ValidationRequest(stock, settings, 3),
                                            operation_active_reader=lambda: False)
    session.source_selection = flow_module._select_v5_source(
        stock, "M3", True, True, clock[0], window_key=key, generation=1)
    pool = flow._pool_from_candles(session, data["candles"][-initial_count:], requested_count=initial_count,
        request_id=query["request_id"], market_data_identity="000250_AL",
        market_source="INTEGRATED", direct_selected_query=query,
        source_verified_at=query["request_as_of"])
    flow._historical_pools[key], flow._validation_sessions[key] = pool, session
    flow._last_run_requests[key] = flow_module.IndicatorFollowSignalValidationRunRequest(settings, 5000)

    window.validation_run_requested.connect(lambda request: flow._run_validation(window, request))
    window.chart_input_invalidated.connect(lambda: flow._invalidate_window_input(window))
    assert flow._ensure_normal_base_history(
        window, session, pool, 5000, on_ready=lambda *args: published.append(args))
    assert len(calls) == 1 and calls[0][1]["interval"] == 3

    def select(minutes):
        with QSignalBlocker(window.basic_signal_interval_combo):
            window.basic_signal_interval_combo.setCurrentText(str(minutes))
        if getattr(window, "_timeframe_transition_target", None) is None:
            window._on_validation_timeframe_changed()
        else:
            window.request_validation()  # A newer programmatic intent while the overlay is held.

    def reply(error=None, *, terminal=True, requested_rows=False, **changes):
        started, options = calls[0]
        clock[0] += timedelta(seconds=1)
        rows = data["candles"]
        if requested_rows:
            assert options["count"] <= len(rows), "use recorded native rows only"
            rows = rows[-options["count"]:]
        response = dict(started, type="minute_candles", interval=3, trcode="opt10080",
            request_id=started["rqname"], received_at=clock[0].isoformat(),
            rows=flow._historical_rows_from_candles(rows),
            rows_count=len(rows), has_more=False)
        if error:
            response.update(ok=False, error=error)
        response.update(changes)
        if terminal:
            broker._pending_tr.pop(started["rqname"], None)
        options["callback"](response)

    def drain():
        for _ in range(4):
            case.app.processEvents()

    try:
        yield SimpleNamespace(window=window, flow=flow, key=key, calls=calls,
            select=select, reply=reply, drain=drain, published=published,
            recorded=data["candles"])
    finally:
        flow._prepare_pending.pop(key, None)
        flow._open_windows.pop(key, None)


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
def test_v5_timeframe_pending_completion_resumes_latest_once(timeframe_pending):
    item = timeframe_pending
    item.select(30)
    assert len(item.calls) == 1
    assert item.window._timeframe_transition_target is not None
    deferred = item.flow._prepare_pending[item.key]
    generation = item.flow._request_generation[item.key]
    initial_status = item.window.validation_status_label.text()
    QTest.qWait(15_500)  # Fire the elapsed notice before a verified late physical completion.
    item.drain()
    assert len(item.calls) == 1
    assert item.flow._prepare_pending.get(item.key) is deferred
    assert item.flow._request_generation[item.key] == generation
    assert item.window._timeframe_transition_target is not None
    assert "대기" in item.window.validation_status_label.text()
    assert item.window.validation_status_label.text() != initial_status
    assert (item.window._timeframe_transition_overlay.findChild(window_module.QLabel).text()
            == item.window.validation_status_label.text())
    item.reply(requested_rows=True)
    assert len(item.calls) == 1  # Completion queues onto the UI thread, never recurses.
    item.drain()
    assert [options["interval"] for _started, options in item.calls] == [3, 30]
    item.reply()  # Duplicate physical callback cannot dispatch again.
    item.drain()
    assert len(item.calls) == 2 and not item.published


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
def test_v5_timeframe_pending_stale_callback_preserves_new_token(timeframe_pending):
    item = timeframe_pending
    item.select(15)
    item.select(30)
    assert len(item.calls) == 1  # Keep only the latest intent while old transport is held.
    deferred_generation = item.flow._request_generation[item.key]
    item.reply(requested_rows=True)
    item.drain()
    assert [options["interval"] for _started, options in item.calls] == [3, 30]
    assert item.flow._request_generation[item.key] == deferred_generation
    assert (item.key, deferred_generation) in item.flow._active_providers
    tokens = set(item.flow._v5_native_tr_pending[item.key])
    generation = item.flow._request_generation[item.key]
    pending_settings = item.window._pending_result_settings_snapshot
    item.reply("CANDLE_TR_BUDGET_EXCEEDED")
    item.reply()
    item.drain()
    assert item.flow._v5_native_tr_pending[item.key] == tokens
    assert item.flow._request_generation[item.key] == generation
    assert item.window._pending_result_settings_snapshot is pending_settings
    assert item.key not in item.flow._v5_manual_retry_required
    assert len(item.calls) == 2 and not item.published


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
def test_v5_timeframe_pending_unproven_completion_requires_manual_recovery(timeframe_pending):
    item = timeframe_pending
    item.select(30)
    # An actual transport timeout retires auto-resume, retaining physical ownership.
    assert item.window._timeframe_transition_target is not None
    item.reply("historical candle request timed out")
    assert "수동" in item.window.validation_status_label.text()
    assert item.window._timeframe_transition_target is None
    for error, changes in (("historical candle request timed out", {}),
                            ("offline transport failed", {}),
                            (None, {"request_id": "unproven"})):
        item.reply(error, **changes)
        item.drain()
    assert len(item.calls) == 1 and not item.published
    assert item.flow._v5_native_tr_pending[item.key]
    assert "수동" in item.window.validation_status_label.text()
    assert "완료 확인 불가" in item.window.validation_status_label.text()
    assert item.window._timeframe_transition_target is None


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
def test_v5_timeframe_terminal_with_older_pages_resumes_latest_once(timeframe_pending):
    item = timeframe_pending
    item.select(15)
    generation = item.flow._request_generation[item.key]
    item.reply(has_more=True, requested_rows=True)
    assert len(item.calls) == 1  # Resume is queued, never nested in the old callback.
    item.drain()
    assert [options["interval"] for _started, options in item.calls] == [3, 15]
    assert item.flow._request_generation[item.key] == generation
    assert (item.key, generation) in item.flow._active_providers
    assert item.window._timeframe_transition_target[1] == "M15"
    assert "수동 재시작" not in item.window.validation_status_label.text()
    tokens = set(item.flow._v5_native_tr_pending[item.key])
    item.reply(has_more=True, requested_rows=True)
    item.drain()
    assert len(item.calls) == 2 and not item.published
    assert item.flow._v5_native_tr_pending[item.key] == tokens
    assert item.key not in item.flow._v5_manual_retry_required


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
@pytest.mark.parametrize("defect", ["pending", "short", "foreign_request", "foreign_token"])
def test_v5_timeframe_terminal_with_older_pages_rejects_unproven_completion(
        timeframe_pending, defect):
    item = timeframe_pending
    item.select(15)
    generation = item.flow._request_generation[item.key]
    changes = {}
    if defect == "short":
        rows = item.flow._historical_rows_from_candles(
            item.recorded[-(item.calls[0][1]["count"] - 1):])
        changes.update(rows=rows, rows_count=len(rows))
    elif defect == "foreign_request":
        changes["request_id"] = "foreign-native-request"
    elif defect == "foreign_token":
        item.flow._v5_native_tr_pending[item.key] = {object()}
    tokens = set(item.flow._v5_native_tr_pending[item.key])
    item.reply(has_more=True, requested_rows=True, terminal=defect != "pending", **changes)
    item.drain()
    assert len(item.calls) == 1 and not item.published
    assert item.flow._request_generation[item.key] == generation
    assert item.flow._v5_native_tr_pending[item.key] == tokens
    assert item.key not in item.flow._v5_manual_retry_required
    if defect == "foreign_token":
        assert item.window._timeframe_transition_target[1] == "M15"
        assert "대기" in item.window.validation_status_label.text()
    else:
        assert "완료 확인 불가" in item.window.validation_status_label.text()
        assert "수동" in item.window.validation_status_label.text()
        assert item.key not in item.flow._prepare_pending


@pytest.mark.parametrize("timeframe_pending", [1000], indirect=True)
@pytest.mark.parametrize("physical_state,complete_rows", [
    ("missing", False), ("non_dict", False), ("released", False),
    ("missing", True), ("non_dict", True),
])
def test_v5_timeframe_terminal_without_release_or_rows_never_resumes(
        timeframe_pending, physical_state, complete_rows):
    item = timeframe_pending
    item.select(15)
    generation = item.flow._request_generation[item.key]
    tokens = set(item.flow._v5_native_tr_pending[item.key])
    broker = item.flow._broker
    if physical_state == "missing":
        del broker._pending_tr
    elif physical_state == "non_dict":
        broker._pending_tr = None
    else:
        broker._pending_tr.pop(item.calls[0][0]["rqname"])
    changes = {}
    if not complete_rows:
        rows = item.flow._historical_rows_from_candles(
            item.recorded[-(item.calls[0][1]["count"] - 1):])
        changes.update(rows=rows, rows_count=len(rows))
    item.reply(has_more=False, requested_rows=True, terminal=False, **changes)
    item.drain()
    assert len(item.calls) == 1 and not item.published
    assert item.flow._request_generation[item.key] == generation
    assert item.flow._v5_native_tr_pending[item.key] == tokens
    assert item.key not in item.flow._prepare_pending
    assert "완료 확인 불가" in item.window.validation_status_label.text()
    assert "수동" in item.window.validation_status_label.text()


def candles(count=600):
    rows = []
    stamp = datetime(2026, 10, 7, 15, 30)
    while len(rows) < count:
        value = 100_000.0 + len(rows)
        rows.append({
            "time": stamp.strftime("%Y%m%d%H%M%S"),
            "open": value,
            "high": value + 10.0,
            "low": value - 10.0,
            "close": value + 1.0,
            "volume": 100,
        })
        stamp -= timedelta(minutes=1)
        if stamp.hour < 9:
            stamp = (stamp - timedelta(days=1)).replace(hour=15, minute=30)
    return list(reversed(rows))


def install(window, rows=None):
    rows = candles() if rows is None else rows
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")


def descriptor(identity, family, label, axis, side, channel, parameters=None):
    return window_module.ValidationFilterDescriptor(
        identity,
        family,
        label,
        axis,
        (side,),
        (channel,),
        json.dumps(parameters or {}, sort_keys=True),
        (),
    )


def canvas_for(case, *, lower_presentation=False):
    rows = candles(8)
    price = descriptor(
        "price-ma5",
        window_module.FAMILY_MOVING_AVERAGE,
        "MA5",
        window_module.PRICE_AXIS,
        "BUY",
        "MA5",
    )
    lower = descriptor(
        "lower-rsi",
        window_module.FAMILY_RSI,
        "RSI",
        window_module.LOWER_AXIS,
        "BUY",
        "RSI",
        {
            "period": 14,
            "criterion_label": "RSI >= 50",
            "condition": {"operator": "GREATER_THAN_OR_EQUAL", "threshold": 50},
        },
    )
    presentations = ()
    if lower_presentation:
        presentations = (window_module.ValidationLowerFilterPresentation(
            key="BUY:rsi",
            side="BUY",
            source_path="buy.rsi",
            filter_token="RSI",
            pane_family=window_module.FAMILY_RSI,
            display_order=(0,),
            descriptor_identities=(lower.identity,),
            component_descriptor_identities=(lower.identity,),
            component_labels=("RSI",),
        ),)
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows,
        [],
        visualization_descriptors=(price, lower),
        visualization_cache=window_module.ValidationIndicatorSeriesCache(
            len(rows),
            (
                (price.identity, "MA5", tuple(row["close"] for row in rows)),
                (lower.identity, "RSI", tuple(45.0 + index for index in range(len(rows)))),
            ),
        ),
        lower_filter_presentations=presentations,
    )
    case.widgets.append(canvas)
    return canvas, price, lower


@pytest.mark.parametrize("client_size", [None, (1464, 782)])
def test_v5_long_summaries_keep_all_actions_in_one_compact_row(case, client_size):
    # Model the Windows UI font instead of offscreen's Helvetica 12 fallback.
    original_font = case.app.font()
    windows_font = QFont("Malgun Gothic", 9)
    case.app.setFont(windows_font)
    try:
        window = window_for(case)
        window.setFont(windows_font)
    finally:
        case.app.setFont(original_font)
    install(window)
    labels = (
        window.validation_status_label,
        window.result_summary_label,
        window.estimated_return_label,
    )
    messages = (
        "Historical source validation completed with full provenance details " * 6,
        "005930 삼성전자 | M240 | 2026-10-01 09:00 ~ 2026-11-09 15:30 | 선택 구간 상세 요약 " * 12,
        "완료 Cycle 123 | 추정 손익 +12.34% | 매수 및 매도 상세 결과 " * 12,
    )
    for label, message in zip(labels, messages):
        label.setText(message)
    window._set_validation_range(0, 3)
    with patch.object(dialog_module.QTimer, "singleShot"):
        window.show()
        if client_size is None:
            target_width = max(1707, window.minimumSizeHint().width())
            target_height = 960
            window.resize(target_width, target_height)
        else:
            # Reproduce the observed client size despite the settings minimum.
            target_width, target_height = client_size
            window.setFixedSize(target_width, target_height)
        case.app.processEvents()

    assert window.width() == target_width
    assert window.height() == target_height
    buttons = (
        window.filter_display_check,
        window.filter_display_valid_check,
        window.reset_button,
        window.primary_validation_action_button,
        window.close_button,
    )
    assert not hasattr(window, "replay_validation_button")
    assert window.run_validation_button is window.primary_validation_action_button
    assert window.primary_validation_action_button.text() == "설정적용"
    assert all(button.isVisible() for button in buttons)
    assert all(button.isEnabled() for button in buttons)
    assert all(window.rect().contains(button.geometry()) for button in buttons)
    for button in buttons:
        center = button.mapTo(window, button.rect().center())
        assert window.childAt(center) is button
        assert button.visibleRegion().contains(button.rect().center())
    centers = [button.geometry().center().y() for button in buttons]
    assert max(centers) - min(centers) <= 1
    assert all(left.geometry().right() < right.x()
               for left, right in zip(buttons, buttons[1:]))
    summary = window.chart_summary_label
    action_row = window._signal_validation_action_layout
    assert action_row.count() == 6
    assert action_row.itemAt(0).widget() is summary
    assert [action_row.itemAt(index).widget() for index in range(1, 6)] == list(buttons)
    assert window.layout().count() == 3
    assert window.layout().itemAt(1).layout() is action_row
    assert window.layout().itemAt(2).widget() is window.result_widget
    assert summary.isVisible() and summary.width() > 0
    assert window.rect().contains(summary.geometry())
    assert summary.alignment() == window_module.Qt.AlignLeft | window_module.Qt.AlignVCenter
    assert summary.geometry().right() < buttons[0].x()
    assert abs(summary.geometry().center().y() - centers[0]) <= 1
    full_text = "\u25B6 " + messages[1] + " " + messages[2]
    assert summary.text() == full_text
    assert summary.toolTip() == full_text
    widths = (target_width,) if client_size else (target_width + 400, target_width)
    for width in widths:
        window.resize(width, target_height)
        case.app.processEvents()
        assert window.width() == width
        assert summary.width() > 0
        assert all(window.rect().contains(button.geometry()) for button in buttons)
        painted_texts = []
        original_painter = window_module.QPainter

        class RecordingPainter(original_painter):
            def drawText(self, *args):
                painted_texts.append(args[-1])
                return super().drawText(*args)

        with patch.object(window_module, "QPainter", RecordingPainter):
            summary.grab()
        assert painted_texts == [summary.fontMetrics().elidedText(
            full_text, window_module.Qt.ElideRight, summary.contentsRect().width(),
        )]
        assert painted_texts[0].endswith("\u2026")
        assert summary.text() == full_text and summary.toolTip() == full_text
    for label, message in zip(labels, messages):
        assert label.text() == message
        assert label.isHidden()
    action_hint = window._signal_validation_action_layout.sizeHint()
    assert action_hint.width() >= sum(button.width() for button in buttons)
    assert action_hint.height() == max(button.height() for button in buttons)


def test_v5_native_title_contains_only_status_and_app_name(case):
    window = window_for(case)
    window.validation_status_label.setText("조회 오류: 응답 대기")
    window.result_summary_label.setText("005930 삼성전자 | 1분봉 | 4캔들")
    window.estimated_return_label.setText("| 손익 +12.34% | 투입 전체 100만원")
    case.app.processEvents()
    assert window.windowTitle() == "조회 오류: 응답 대기 | 지표추종매매 - 독립 신호검증 V5"
    window.validation_status_label.setText("")
    case.app.processEvents()
    assert window.windowTitle() == "지표추종매매 - 독립 신호검증 V5"


def test_v5_chart_summary_before_selection_uses_current_stock_and_timeframe(case):
    window = window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("1분")
    install(window)
    assert window._chart_summary_text() == "\u25B6 005930 삼성전자 | 1분봉"
    case.app.processEvents()
    assert window.chart_summary_label.text() == "\u25B6 005930 삼성전자 | 1분봉"
    assert window.chart_summary_label.toolTip() == "\u25B6 005930 삼성전자 | 1분봉"
    # A pending timeframe request must show the selected frame, not old results.
    window.basic_signal_interval_combo.setCurrentText("5분")
    assert window._chart_summary_text() == "\u25B6 005930 삼성전자 | 5분봉"
    case.app.processEvents()
    assert window.chart_summary_label.text() == "\u25B6 005930 삼성전자 | 5분봉"


@pytest.mark.parametrize("clear_action", ["reset", "timeframe", "drag"])
def test_v5_chart_summary_clears_range_metrics(case, clear_action):
    window = window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("1분")
    install(window)
    window._set_validation_range(596, 599)
    window._range_replay_entries_available = True
    window._refresh_validation_range_results()
    selection_text = window.result_summary_label.text()
    financial_text = window.estimated_return_label.text()
    assert "4캔들 | 매수신호 0 | 매도신호 0" in selection_text
    assert "손익" in financial_text and "투입 전체" in financial_text
    assert window._chart_summary_text() == "\u25B6 " + selection_text + " " + financial_text
    case.app.processEvents()
    assert window.chart_summary_label.text() == "\u25B6 " + selection_text + " " + financial_text
    assert window.chart_summary_label.toolTip() == "\u25B6 " + selection_text + " " + financial_text
    if clear_action == "reset":
        window.reset_button.click()
    elif clear_action == "timeframe":
        window.basic_signal_interval_combo.setCurrentText("5분")
    else:
        window._begin_validation_range_drag(590)
    assert window.validation_range is None
    assert window.estimated_return_label.text() == ""
    assert "캔들" not in window.result_summary_label.text()
    timeframe = "5분봉" if clear_action == "timeframe" else "1분봉"
    assert window._chart_summary_text() == "\u25B6 005930 삼성전자 | " + timeframe
    case.app.processEvents()
    assert window.chart_summary_label.text() == "\u25B6 005930 삼성전자 | " + timeframe


@pytest.mark.parametrize("selected", [False, True])
def test_v5_chart_paint_does_not_draw_or_reserve_space_for_summary(case, selected):
    window = window_for(case)
    install(window)
    if selected:
        window._set_validation_range(596, 599)
        window._range_replay_entries_available = True
        window._refresh_validation_range_results()
    canvas = window.canvas
    assert canvas._input_frame_owner is window
    canvas.setFont(QFont("Malgun Gothic", 9))
    canvas.resize(700, 500)
    canvas.grab()  # Settle the scroll area's pending canvas geometry first.
    plot_top = canvas.price_scale().plot_top
    legends = canvas._price_legend_layout(canvas.price_scale(), canvas.font())
    window._window_title_timer.stop()
    with patch.object(window, "_chart_summary_text", return_value=""):
        without_summary = canvas.grab().toImage()
    with patch.object(window, "_chart_summary_text", return_value="Summary must stay outside the chart") as summary:
        with_summary = canvas.grab().toImage()
        summary.assert_not_called()
    assert without_summary == with_summary
    assert canvas.price_scale().plot_top == plot_top
    assert canvas._price_legend_layout(canvas.price_scale(), canvas.font()) == legends
    assert not canvas.findChildren(window_module.QLabel)


def test_v5_window_defaults_to_250_visible_candles_with_3500_cap(case):
    window = window_for(case)
    install(window)

    assert window.visible_candle_span == 250
    assert window.visible_start_index == len(window._candles) - 250
    assert window._MAX_VISIBLE_CANDLE_SPAN == 3500


def test_v5_negative_zero_is_display_only_and_preserves_underlying_value(case):
    threshold = -0.00001
    lower = descriptor(
        "ocr-negative-zero",
        window_module.FAMILY_OCR_OSC,
        "OCR",
        window_module.LOWER_AXIS,
        "BUY",
        "CRITERION",
        {"condition": {
            "operator": "LESS_THAN_OR_EQUAL",
            "target": "OSC",
            "threshold": threshold,
        }},
    )
    rows = candles(4)
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows,
        [],
        visualization_descriptors=(lower,),
        visualization_cache=window_module.ValidationIndicatorSeriesCache(
            len(rows),
            ((lower.identity, "CRITERION", (threshold,) * len(rows)),),
        ),
    )
    case.widgets.append(canvas)
    pane, = canvas.lower_pane_records()
    scale = canvas._lower_scale(
        window_module.FAMILY_OCR_OSC,
        int(pane["top"]),
        int(pane["bottom"]),
    )
    record, = canvas.lower_reference_line_records(
        window_module.FAMILY_OCR_OSC,
        scale,
    )

    assert canvas._crosshair_number_text(threshold) == "0"
    assert record["text"].endswith(" 0")
    assert record["value"] == threshold
    assert record["value"] < 0


def test_v5_unified_price_lower_multiselect_and_valid_zero_mode(case):
    canvas, price, lower = canvas_for(case)
    both = {("BUY", price.identity), ("BUY", lower.identity)}

    canvas._toggle_price_legend_identities(
        "BUY",
        (price.identity, lower.identity),
    )
    assert set(canvas.highlighted_price_filter_identities) == {("BUY", price.identity)}
    assert set(canvas.highlighted_lower_filter_identities) == {("BUY", lower.identity)}
    assert canvas.hide_unhighlighted_price_filters

    canvas.set_valid_filter_identities((), valid_mode_enabled=True)
    assert canvas.valid_filter_identities == ()
    assert canvas._price_visibility_filter_active()
    assert canvas._effective_price_filter_identities() == set()
    assert canvas._effective_lower_filter_identities() == set()
    assert set(canvas.hidden_price_filter_identities) == {price.identity}

    canvas.set_valid_filter_identities((), valid_mode_enabled=False)
    assert canvas._effective_price_filter_identities() | canvas._effective_lower_filter_identities() == both
    assert canvas.hidden_price_filter_identities == ()


def test_v5_not_styles_outer_red_inner_yellow_in_legend_only(case):
    canvas, price, lower = canvas_for(case, lower_presentation=True)
    series_before = (canvas._price_series_groups, canvas._lower_series_groups)
    line_legend_before = canvas.legend_records()
    canvas.set_not_filter_identities(
        {("BUY", price.identity), ("BUY", lower.identity)},
        {("BUY", lower.identity)},
    )

    blocks = next(blocks for side, _label, blocks in canvas._price_legend_rows()
                  if side == "BUY")
    price_block = next(block for block in blocks if price.identity in block["descriptor_identities"])
    lower_block = next(block for block in blocks if block["legend_role"] == "LOWER_FILTER")
    rect = QRectF(0.0, 0.0, 1000.0, 24.0)
    red_runs = canvas._layout_price_legend_blocks([price_block], rect, canvas.font())[0]["caption_runs"]
    yellow_runs = canvas._layout_price_legend_blocks([lower_block], rect, canvas.font())[0]["caption_runs"]

    assert {run["color"].getRgb() for run in red_runs} == {
        window_module._NOT_FILTER_TEXT.getRgb()
    }
    assert window_module._INNER_NOT_FILTER_TEXT.getRgb() in {
        run["color"].getRgb() for run in yellow_runs
    }
    assert window_module._NOT_FILTER_TEXT.getRgb() not in {
        run["color"].getRgb() for run in yellow_runs
    }
    assert (canvas._price_series_groups, canvas._lower_series_groups) == series_before
    assert canvas.legend_records() == line_legend_before


def test_v5_vertical_navigation_preserves_marking_horizontal_change_disables_it(case):
    window = window_for(case)
    install(window)
    with QSignalBlocker(window.marking_display_check):
        window.marking_display_check.setChecked(True)
    period = (window._candles[350]["time"], window._candles[599]["time"])
    frozen = object()
    window._marking_times = period
    window._marking_snapshot = frozen
    generation = window._marking_generation

    window._pan_chart_view(0.0, 20.0)
    assert window.marking_display_check.isChecked()
    assert window._marking_times == period
    assert window._marking_snapshot is frozen
    assert window._marking_generation == generation

    with patch.object(window_module, "signal_validation_trace_enabled", return_value=False):
        window._set_time_view(
            window.visible_start_index - 10,
            window.visible_candle_span,
            user_horizontal_navigation=True,
        )
    assert not window.marking_display_check.isChecked()
    assert window._marking_times is None
    assert window._marking_snapshot is None
    assert window._marking_generation == generation + 1


@pytest.fixture
def m30_marking_range(case, monkeypatch, request):
    """Recorded native bars through real workers; scheduling and I/O are isolated."""
    minutes = getattr(request, "param", 30)
    path = Path(__file__).resolve().parents[1] / f"runtime/signal_validation_v2_history_cache/000250_AL_{minutes}_5900.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sidecar = json.loads(Path(str(path) + ".market-source.json").read_text(encoding="utf-8"))
    assert data["timeframe_key"] == f"M{minutes}"
    rules, state = deepcopy(case.rules), deepcopy(case.ui_state)
    rules["bar"]["bar_minutes"] = minutes
    rules["validation_timeframe"] = {"key": f"M{minutes}"}
    rules["validation_market_scope"] = {"regular_market_only": True}
    state["basic"]["basic_signal_interval_combo"] = str(minutes)
    state["basic"]["buy_signal_expr_line"] = "D"
    state["basic"]["sell_signal_expr_line"] = "A"
    state["buy_ui"]["signal_filter"].update(
        buy_ocr_enabled=False, buy_bollinger_enabled=False,
        buy_ma_enabled=False, buy_rsi_enabled=True,
        buy_rsi_value_line="45")
    for condition in state["sell_ui"]["signal_conditions"].values():
        for name in condition:
            if name.endswith("_check"):
                condition[name] = False
    state["sell_ui"]["signal_conditions"]["condition_a"].update(
        rsi_check=True, rsi_compare_combo="이상", rsi_value_line="55")
    state["validation_market_scope"] = {"regular_market_only": True}
    with patch.object(dialog_module.QTimer, "singleShot"):
        window = window_module.IndicatorFollowSignalValidationV5Window(
            flow_module.ValidationStockRef("000250", f"M{minutes} recorded"), case._seed(rules, state))
    case.widgets.append(window)
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(), host=_FakeHost(window.stock),
        recent_stock_store=support._MemoryRecentStockStore(),
        preference_store=SimpleNamespace(), historical_cache=SimpleNamespace(),
        production_session_contract_reader=lambda *_args: {
            "status": "PRODUCTION_SESSION_UNAVAILABLE", "ready": False},
    )
    flow._now_factory = lambda: datetime.fromisoformat(sidecar["direct_selected_query"]["request_as_of"])
    flow._operation_active_reader = lambda: False
    flow._validation_source_by_code[window.stock.code] = True
    key = id(window)
    flow._open_windows[key] = window
    flow._request_generation[key] = 1
    flow._history_targets[key] = 5000
    jobs = []

    class HeldThread:
        def __init__(self, *, target, name, **_kwargs):
            self.target, self.name = target, name

        def start(self):
            jobs.append((self.name, self.target))

    monkeypatch.setattr(flow_module, "Thread", HeldThread)
    monkeypatch.setattr(window_module, "Thread", HeldThread)
    window.marking_evaluation_requested.connect(lambda request: flow._start_marking(window, request))
    window.validation_range_evaluation_requested.connect(
        lambda start, end: flow._run_cached_range_replay(window, start, end))
    window.validation_range_changed.connect(lambda value: flow._invalidate_window_range(window, value))

    def drain():
        for _ in range(40):
            case.app.processEvents()
            if not jobs:
                break
            _name, run = jobs.pop(0)
            run()
        assert not jobs, "offline worker queue did not settle"

    settings = window._marking_current_settings()
    session = flow_module.ValidationSession(
        flow_module.ValidationRequest(window.stock, settings, minutes), operation_active_reader=lambda: False)
    pool = flow._pool_from_candles(session, data["candles"], requested_count=5900,
        request_id=sidecar["direct_selected_query"]["request_id"],
        market_data_identity="000250_AL", market_source="INTEGRATED",
        direct_selected_query=sidecar["direct_selected_query"])
    view, _deficit = flow._normal_base_history_view(session, pool, 5000)

    def publish():
        window._normal_base_requested = True
        flow._use_pool_for_window(window, session, pool, evaluation_count=5000, _normal_view=view)
        drain()

    publish()
    if minutes == 3:
        start, end = 3061, 3117
    else:
        start = next(i for i, row in enumerate(window._candles) if row["time"] == "20261019133000")
        end = next(i for i, row in enumerate(window._candles) if row["time"] == "20261029140000")
        assert end - start + 1 == 106
    window._set_time_view(start - 60.25, 250, manually_adjusted=True)
    drain()
    window.marking_display_check.click()
    drain()
    assert window.marking_display_check.isChecked(), window.marking_display_check.toolTip()
    window._begin_validation_range_drag(start)
    window._complete_validation_range_drag(start, end)
    drain()
    assert window.validation_range == (start, end)
    assert window._range_replay_entries_available
    assert minutes == 3 or window.completed_cycles, (
        window.validation_status_label.text(), window.result_summary_label.text(),
        window.estimated_return_label.text(), window._range_replay_entries_available,
        [(entry.signal, entry.evaluation_index) for entry in window._range_replay_entries],
    )
    window.marking_display_check.click()
    drain()
    assert not window.marking_display_check.isChecked()

    def results():
        left, right = window.canvas._visible_index_bounds()
        return dict(
            range_times=tuple(window._candles[i]["time"] for i in window.validation_range),
            counts=window.result_summary_label.text(),
            financial=window.estimated_return_label.text(),
            cells=tuple(tuple(window.completed_cycle_table.item(row, col).text()
                              for col in range(window.completed_cycle_table.columnCount()))
                        for row in range(window.completed_cycle_table.rowCount())),
            cycles=window.completed_cycles,
            visible=tuple(row["time"] for row in window._candles[left:right]),
            start=window.visible_start_index, span=window.visible_candle_span,
            scroll=window.time_navigation_scrollbar.value(),
            start_boundary=window.canvas.selection_start_time_record(),
            boundary=window.canvas.selection_end_time_record(),
        )

    return SimpleNamespace(window=window, flow=flow, publish=publish, drain=drain, results=results)


@pytest.mark.parametrize("republish", [False, True], ids=["same-source", "rebind"])
def test_v5_m30_marking_roundtrip_preserves_selected_results_and_view(m30_marking_range, republish):
    item = m30_marking_range
    before = item.results()
    assert before["financial"] and before["cells"]
    assert before["start_boundary"] and before["boundary"]
    item.window.marking_display_check.click()
    if republish:
        item.publish()
    item.drain()
    after = item.results()
    print("M30_ROUNDTRIP", republish, {key: (before[key], after[key])
          for key in before if before[key] != after[key]})
    assert item.window.marking_display_check.isChecked()
    assert after == before
    # A second real OFF/ON also covers the local marking cache hit.
    item.window.marking_display_check.click()
    item.drain()
    assert item.results() == before
    item.window.marking_display_check.click()
    item.drain()
    assert item.window.marking_display_check.isChecked()
    assert item.results() == before


@pytest.mark.parametrize("activation", ["mouse", "toggle"])
@pytest.mark.parametrize("focus", ["expression", "canvas"])
@pytest.mark.parametrize("m30_marking_range", [3], indirect=True)
def test_v5_m3_marking_widget_roundtrip_preserves_dragged_period(
        case, m30_marking_range, activation, focus):
    item = m30_marking_range
    window = item.window
    with patch.object(dialog_module.QTimer, "singleShot"):
        window.show()
        window.resize(1707, 960)
        case.app.processEvents()
    window._set_time_view(3030, 150, manually_adjusted=True)
    canvas = window.canvas
    y = int((canvas.price_scale().plot_top + canvas.price_scale().plot_bottom) / 2)
    start, end = 3061, 3117
    QTest.mouseDClick(canvas, window_module.Qt.LeftButton,
                     pos=window_module.QPoint(int(canvas._x_for_index(start)), y))
    QTest.mouseRelease(canvas, window_module.Qt.LeftButton,
                       pos=window_module.QPoint(int(canvas._x_for_index(end)), y))
    item.drain()
    assert window.validation_range == (start, end)
    assert canvas.validation_range == (start, end)
    before = item.results()
    assert "57" in before["counts"]
    target = window.buy_signal_expr_line if focus == "expression" else canvas
    target.setFocus()
    case.app.processEvents()
    assert case.app.focusWidget() is target
    fingerprint = window._current_signal_ui_fingerprint()
    assert fingerprint == window._confirmed_signal_ui_fingerprint
    button = window.marking_display_check
    assert isinstance(button, window_module.QPushButton)
    assert button.isCheckable() and button.focusPolicy() == window_module.Qt.NoFocus
    assert not button.isChecked()
    with patch.object(window, "_queue_signal_validation_ui_confirmation",
                      wraps=window._queue_signal_validation_ui_confirmation) as queued, \
            patch.object(window, "_on_signal_validation_ui_changed",
                         wraps=window._on_signal_validation_ui_changed) as changed:
        for checked in (True, False):
            if activation == "mouse":
                QTest.mouseClick(button, window_module.Qt.LeftButton)
            else:
                button.toggle()  # Non-mouse toggled path; not a Windows UIA invocation.
            item.drain()
            assert button.isChecked() is checked, button.toolTip()
            assert window._current_signal_ui_fingerprint() == fingerprint
            assert window._confirmed_signal_ui_fingerprint == fingerprint
            assert window.validation_range == (start, end)
            assert canvas.validation_range == (start, end)
            assert item.results() == before
        assert queued.call_count == (2 if activation == "mouse" and focus == "expression" else 0)
        changed.assert_not_called()


def test_v5_flow_owns_injected_stores_and_purges_startup_evictions(case):
    class RecentStore:
        recent_stocks = (case.stock,)

        @staticmethod
        def take_startup_evicted_codes():
            return ("000660",)

    class HistoricalCache:
        def __init__(self):
            self.deleted = []

        def delete_stock(self, code):
            self.deleted.append(code)
            return {"ok": True}

    recent = RecentStore()
    preferences = object()
    history = HistoricalCache()
    flow = flow_module.IndicatorFollowSignalValidationV5Flow(
        object(),
        host=_FakeHost(case.stock),
        recent_stock_store=recent,
        preference_store=preferences,
        historical_cache=history,
    )

    assert flow._recent_stock_store is recent
    assert flow._preference_store is preferences
    assert flow._historical_cache is history
    assert flow.recent_stocks == (case.stock,)
    assert history.deleted == ["000660"]
    assert flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS == {}


def test_v5_range_liquidation_toggle_updates_only_selected_results(case):
    window = window_for(case)
    rows = candles()
    for offset, row in enumerate(rows[-5:]):
        row["time"] = f"2026102709{offset:02d}00"
    rows[-1]["close"] = 150_000.0
    install(window, rows)
    window._result_settings_snapshot = None  # Exercise the legacy completed-cycle branch.

    def entry(side, index):
        return window_module.ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=rows[index]["time"],
            signal=side,
            reason="fixture",
            signal_index=index,
            signal_time=rows[index]["time"],
            delay_bar=0,
            matched_groups=[],
            details=[],
            trace={"conditions": [], "groups": [], "aggregations": []},
        )

    entries = [entry("BUY", 595), entry("BUY", 596),
               entry("SELL", 597), entry("BUY", 598)]
    window._set_validation_range(596, 599)
    window._range_replay_entries_available = True
    with patch.object(window, "_marker_entries_for_display", return_value=entries), \
            patch.object(window, "_refresh_signal_tooltips_only") as tooltips, \
            patch.object(window_module.QTimer, "singleShot"), \
            patch.object(window, "_on_signal_validation_ui_changed") as revalidate:
        window._refresh_validation_range_results()
        original_cycles = window.completed_cycles
        original_summary = window.estimated_return_label.text()
        assert window._chart_summary_text() == (
            "\u25B6 " + window.result_summary_label.text() + " " + original_summary
        )
        assert not window.validation_range_liquidation_check.isChecked()
        assert len(original_cycles) == 1
        assert window.canvas._liquidation_marker_records() == []
        end_label = window.canvas.selection_end_time_record()
        assert end_label["label"] == "10/27 09:04"
        assert window.completed_cycle_table.item(0, 0).text() == "1"

        tooltip_calls = tooltips.call_count
        window.validation_range_liquidation_check.setChecked(True)
        assert tooltips.call_count == tooltip_calls
        assert window.validation_range == (596, 599)
        assert len(window.completed_cycles) == 2
        assert window.completed_cycles[0] == original_cycles[0]
        assert tuple(window.canvas._completed_cycle_average_lines) == window.completed_cycles
        forced = window.completed_cycles[-1]
        assert forced.is_liquidation
        assert forced.buy_indexes == (598,)
        assert forced.sell_index == 599
        assert forced.sell_price == 150_000.0
        assert window.canvas._liquidation_marker_records() == [
            {"evaluation_index": 599, "side": "SELL", "virtual_liquidation": True}]
        assert window.canvas.selection_end_time_record() == end_label
        assert window.completed_cycle_table.item(1, 0).text() == "청산"
        assert window.completed_cycle_table.item(1, 5).text() == "10/27 09:04"
        assert window.completed_cycle_table.item(1, 6).text() == "150,000"
        assert window.estimated_return_label.text() != original_summary
        assert [(item.signal, item.evaluation_index) for item in entries] == [
            ("BUY", 595), ("BUY", 596), ("SELL", 597), ("BUY", 598),
        ]

        window._validation_range = (596, 598)
        window._refresh_validation_range_results()
        assert window.completed_cycles[-1].is_liquidation
        assert window.completed_cycles[-1].sell_index == 598
        assert window.completed_cycles[-1].sell_price == rows[598]["close"]
        window._validation_range = (596, 597)
        window._refresh_validation_range_results()
        assert len(window.completed_cycles) == 1
        assert not window.completed_cycles[0].is_liquidation
        assert window.canvas._liquidation_marker_records() == []
        window._validation_range = (596, 599)
        window._refresh_validation_range_results()

        tooltip_calls = tooltips.call_count
        window.validation_range_liquidation_check.setChecked(False)
        assert tooltips.call_count == tooltip_calls
        assert window.completed_cycles == original_cycles
        assert tuple(window.canvas._completed_cycle_average_lines) == original_cycles
        assert window.canvas._liquidation_marker_records() == []
        assert window.canvas.selection_end_time_record() == end_label
        assert window.estimated_return_label.text() == original_summary
        window.validation_range_liquidation_check.setChecked(True)
        window._validation_range = None
        window._refresh_validation_range_results()
        assert window.completed_cycles == ()
        assert window.canvas._completed_cycle_average_lines == ()
        assert window.completed_cycle_table.rowCount() == 0

        rules = case._seed().settings_snapshot.to_dict()
        rules["validation_execution"] = {
            "enabled": True, "first_buy_quantity": 3,
        }
        window._result_settings_snapshot = window_module.ValidationSettingsSnapshot(rules)
        window._validation_range = (596, 599)
        window._refresh_validation_range_results()
        assert window.completed_cycles[-1].is_liquidation
        assert window.completed_cycles[-1].buy_quantity == 3
        assert window.completed_cycles[-1].buy_cost == rows[598]["close"] * 3
        revalidate.assert_not_called()


def test_v5_execution_cycle_projection_marks_only_forced_last_cycle(case):
    rows = candles(3)
    entries = [window_module.ValidationReplayEntry(
        evaluation_side="BUY", evaluation_index=0,
        evaluation_time=rows[0]["time"], signal="BUY", reason="fixture",
        signal_index=0, signal_time=rows[0]["time"], delay_bar=0,
        matched_groups=[], details=[],
        trace={"conditions": [], "groups": [], "aggregations": []},
    )]
    cycles = window_module.validation_execution_cycles(
        rows, entries, {"enabled": True, "first_buy_quantity": 3},
        force_liquidation_at_end=True,
    )
    assert len(cycles) == 1
    assert cycles[0].is_liquidation
    assert cycles[0].buy_quantity == 3


def test_v5_header_pipes_share_spacing_and_separate_liquidation(case):
    window = window_for(case)
    layout = window.basic_header_row
    widgets = [layout.itemAt(index).widget() for index in range(layout.count())]
    pipes = [widget for widget in widgets
             if isinstance(widget, window_module.QLabel) and widget.text() == "|"]
    assert layout.spacing() == 8
    assert len(pipes) == 6
    window.basic_header_widget.resize(2000, 40)
    assert layout.activate()
    first_pipe = pipes[0]
    for pipe in pipes:
        assert pipe.contentsMargins() == first_pipe.contentsMargins()
        assert pipe.styleSheet() == first_pipe.styleSheet()
        assert pipe.minimumWidth() == pipe.maximumWidth() == 18
        assert pipe.minimumHeight() == pipe.maximumHeight() == 30
        assert pipe.font().pointSize() == 10
        assert not pipe.font().bold()
        assert pipe.font() == first_pipe.font()
        assert "padding: 0px;" in pipe.styleSheet()
        assert pipe.fontMetrics().horizontalAdvance("|") <= 18
        index = widgets.index(pipe)
        left, right = widgets[index - 1], widgets[index + 1]
        assert pipe.geometry().left() - left.geometry().right() - 1 == 8
        assert right.geometry().left() - pipe.geometry().right() - 1 == 8
        assert pipe.alignment() & window_module.Qt.AlignHCenter
        glyph_margin = (pipe.width() - pipe.fontMetrics().horizontalAdvance("|")) / 2
        assert glyph_margin >= 0
    assert widgets.index(window.validation_regular_market_separator) + 1 == widgets.index(
        window.validation_range_liquidation_check)
    dummy = window.validation_regular_market_dummy_check
    index = widgets.index(window.validation_range_liquidation_check)
    assert widgets[index + 1] is pipes[-1]
    assert widgets[index + 2] is dummy
    assert type(dummy) is window_module.QCheckBox
    assert dummy.text() == "정규장만 표시"
    assert dummy.isEnabled() and dummy.isCheckable() and not dummy.isChecked()
    assert dummy.styleSheet() == dummy.toolTip() == dummy.objectName() == ""
    assert dummy.focusPolicy() == window_module.Qt.TabFocus
    assert not hasattr(window, "validation_regular_market_only_check")


def test_v5_liquidation_sell_marker_is_visual_only_and_viewport_bound(case):
    from PyQt5.QtGui import QPainter, QPixmap

    rows = candles(8)
    markers = [{"evaluation_index": 2, "side": "BUY"},
               {"evaluation_index": 4, "side": "SELL"}]
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows, markers, timeframe_key="M1")
    case.widgets.append(canvas)
    canvas.resize(800, 400)
    canvas.set_validation_range(2, 7)
    assert canvas._liquidation_marker_records() == []
    cycle = window_module.IndicatorFollowValidationCompletedCycle(
        cycle_number=1, buy_indexes=(6,), buy_start_index=6,
        buy_end_index=6, buy_count=1, average_buy_price=100_006.0,
        sell_index=7, sell_price=100_007.0, estimated_return_percent=0.0,
        is_liquidation=True)
    canvas.set_completed_cycle_average_lines((cycle,))
    original = canvas.marker_records()
    assert canvas._liquidation_marker_records() == [
        {"evaluation_index": 7, "side": "SELL", "virtual_liquidation": True}]
    assert canvas.marker_count("SELL") == 1
    assert canvas.marker_records() == original
    pixmap = QPixmap(canvas.size())
    painter = QPainter(pixmap)
    try:
        canvas._paint_static_chart(painter, canvas.price_scale())
    finally:
        painter.end()
    assert not pixmap.isNull()
    canvas.set_time_view(0, 5)
    assert canvas._liquidation_marker_records() == []
    canvas.set_time_view(3, 5)
    assert len(canvas._liquidation_marker_records()) == 1
    canvas.set_completed_cycle_average_lines((
        window_module.IndicatorFollowValidationCompletedCycle(
            cycle_number=1, buy_indexes=(6,), buy_start_index=6,
            buy_end_index=6, buy_count=1, average_buy_price=100_006.0,
            sell_index=7, sell_price=100_007.0, estimated_return_percent=0.0),))
    assert canvas._liquidation_marker_records() == []
    canvas.set_completed_cycle_average_lines((cycle,))
    canvas.set_validation_range(None, None)
    assert canvas._liquidation_marker_records() == []
    assert canvas.marker_records() == original


def test_v5_selected_end_time_label_uses_exact_bar_and_follows_view(case):
    rows = candles(8)
    rows[7]["time"] = "20261027163300"
    canvas = window_module.IndicatorFollowSignalValidationChartCanvas(
        rows, [], timeframe_key="M1")
    case.widgets.append(canvas)
    canvas.resize(800, 400)
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(2, 7)
    record = canvas.selection_end_time_record()
    assert record == {"index": 7, "x": canvas._x_for_index(7),
                      "label": "10/27 16:33"}
    canvas.set_time_view(0, 5)
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(2, 4)
    left_x = canvas.selection_end_time_record()["x"]
    canvas.set_time_view(3, 5)
    assert canvas.selection_end_time_record()["x"] != left_x
    assert canvas.selection_end_time_record()["x"] == canvas._x_for_index(4)
    canvas._candles[4]["time"] = "bad"
    assert canvas.selection_end_time_record() is None
    canvas.set_validation_range(None, None)
    assert canvas.selection_end_time_record() is None


def test_v5_m30_binding_wait_retains_identical_selected_financials(m30_marking_range):
    item = m30_marking_range
    window, flow = item.window, item.flow
    bounds = tuple(next(i for i, row in enumerate(window._candles) if row["time"] == stamp)
                   for stamp in ("20261021103000", "20261102100000"))
    window._complete_validation_range_drag(*bounds)
    item.drain()
    before = item.results()
    assert bounds[1] - bounds[0] + 1 == 104
    assert before["financial"] and before["cells"]
    key = id(window)
    pool, session = flow._historical_pools[key], flow._validation_sessions[key]
    window._marking_cache = None  # First ON must reach the binding wait, not a fixture cache hit.
    flow._native_selected_pending[key] = (session, pool, flow._request_generation[key])
    for checked in (True, False, True):
        window.marking_display_check.click()
        assert window.marking_display_check.isChecked() is checked
        item.drain()
        assert item.results() == before
    flow._native_selected_pending.pop(key)
    item.publish()
    assert window.marking_display_check.isChecked()
    assert window._marking_snapshot is not None
    assert item.results() == before


def test_v5_m30_unknown_binding_replays_range_and_rejects_stale_completion(
        m30_marking_range):
    item = m30_marking_range
    window, flow = item.window, item.flow
    bounds = tuple(next(i for i, row in enumerate(window._candles) if row["time"] == stamp)
                   for stamp in ("20261021103000", "20261102100000"))
    window._complete_validation_range_drag(*bounds)
    item.drain()
    before = item.results()
    key = id(window)
    pool, session = flow._historical_pools[key], flow._validation_sessions[key]
    held = []
    with patch.object(flow, "_emit_range_replay_completed", side_effect=held.append):
        flow._run_cached_range_replay(window, *bounds)
        item.drain()
    assert len(held) == 1 and held[0]["snapshot"] is not None
    old_entries = tuple(window._range_replay_entries)
    window._marking_cache = None
    window._v5_marking_display_publication = None
    flow._native_selected_pending[key] = (session, pool, flow._request_generation[key])
    window.marking_display_check.click()
    assert window._v5_marking_binding_pending
    assert window.estimated_return_label.text() == ""
    assert window.completed_cycles == ()
    flow._on_range_replay_completed(held[0])
    assert window.estimated_return_label.text() == ""
    assert window.completed_cycle_table.rowCount() == 0
    # Publish a newly verified receipt for the recorded bars, without a chart reinstall.
    flow._native_selected_pending.pop(key, None)
    pool["request_id"] += ":reverified"
    flow._pool_dispatch_snapshot(pool)
    window._v5_marking_display_publication = (
        pool, session, flow._request_generation[key], pool["_dispatch_snapshot"])
    window._v5_marking_binding_pending = False
    with patch.object(flow, "_prepare_replay_result_from_pool",
                      wraps=flow._prepare_replay_result_from_pool) as replay:
        window.marking_display_check.click()
        window.marking_display_check.click()
        item.drain()
        assert replay.call_count >= 1
    assert window._marking_snapshot is not None
    assert item.results() == before
    assert window._range_replay_entries and window._range_replay_entries[0] is not old_entries[0]
    flow._on_range_replay_completed(held[0])
    assert item.results() == before
    # A real source publication must also restore the independent range with Mark OFF.
    window.marking_display_check.click()
    window._marking_cache = None
    window._v5_marking_display_publication = None
    flow._native_selected_pending[key] = (session, pool, flow._request_generation[key])
    window.marking_display_check.click()
    window.marking_display_check.click()
    assert window.estimated_return_label.text() == ""
    flow._native_selected_pending.pop(key, None)
    item.publish()
    assert not window.marking_display_check.isChecked()
    assert item.results() == before


def test_v5_m15_first_provenance_failure_preserves_rejection():
    """Diagnostic removal must fail; no Qt objects, broker calls or trace files."""
    flow_type = flow_module.IndicatorFollowSignalValidationV5Flow
    flow = SimpleNamespace(
        _pool_computation_source_identity=lambda _pool: (),
        _period_session_scope=flow_type._period_session_scope,
        _sor_m15_after_anchor_verified=flow_type._sor_m15_after_anchor_verified,
        _direct_selected_query_valid=lambda *_args: False,
    )
    settings = SimpleNamespace(to_dict=lambda: {})
    session = SimpleNamespace(request=SimpleNamespace(
        stock=SimpleNamespace(code="005930"), timeframe_key="M15",
        timeframe_minutes=15, settings_snapshot=settings))
    as_of = datetime.fromisoformat("2026-11-09T20:00:00+09:00")
    error = "SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID"
    cases = [
        ("ROW_TYPE", [None], None, None, None, None),
        ("STAMP_FORMAT", [{"time": "private-invalid-time"}], None, None, None, None),
        ("STAMP_ORDER_OR_DUPLICATE", [{"time": "20261109090000"}] * 2,
         None, None, None, None),
        ("INCOMPLETE_ROW", [{"time": "20261109090000", "is_complete": False}],
         None, None, None, None),
        ("DISPLAY_MISMATCH", [{"time": "20261109090000"}], None, None, None, None),
        ("STAMP_PARSE", [{"time": "20261109999900"}], 0, "20261109999900", None, None),
        ("SESSION_UNKNOWN", [{"time": "20261109070000"}], 0, "20261109070000", None, 930),
        ("SECOND_NONZERO", [{"time": "20261109090001"}], 0, "20261109090001", "KRX", 540),
        ("ANCHOR_MISMATCH", [{"time": "20261109154000"}], 0,
         "20261109154000", "NXT_AFTER", 930),
        ("ROW_IDENTITY", [{"time": "20261109090000", "market_data_identity": "private"}],
         0, "20261109090000", "KRX", 540),
        ("ROW_SOURCE", [{"time": "20261109090000", "market_source": "private"}],
         0, "20261109090000", "KRX", 540),
        ("ROW_TIMEFRAME", [{"time": "20261109090000", "timeframe_minutes": 1}],
         0, "20261109090000", "KRX", 540),
        ("COMPLETION_AFTER_ASOF", [{"time": "20261109194000"}],
         0, "20261109194000", "NXT_AFTER", 940),
    ]
    records = []
    with patch.object(flow_module, "trace_signal_validation_event",
                      side_effect=lambda event, **fields: records.append((event, fields))):
        for case_number, (reason, rows, index, stamp, row_session, anchor) in enumerate(cases, 1):
            pool = dict(stock=session.request.stock, candles=[{"time": "20261106090000"}],
                        market_source="INTEGRATED", market_data_identity="005930_AL",
                        timeframe_key="M15", timeframe_minutes=15)
            view = dict(pool, candles=rows, display_candles=rows)
            if reason == "DISPLAY_MISMATCH":
                view["display_candles"] = [{"time": "20261109091500"}]
            if reason == "ANCHOR_MISMATCH":
                # Existing proof selects 15:30; observed start is 15:40.
                pool["candles"] = [{"time": t} for t in
                                   ("20261106153000", "20261106154500")]
                rows[0]["market_source"] = "private-later-failure"
            cutoff = (datetime.fromisoformat("2026-11-09T19:50:00+09:00")
                      if reason == "COMPLETION_AFTER_ASOF" else as_of)
            context = flow_module._V5M3ProvenanceTrace(17)
            before = deepcopy((pool, view))
            for _ in range(2):
                try:
                    flow_type._sor_m1_calculation_view(
                        flow, session, pool, view, ((), cutoff, {}),
                        _provenance_trace=context)
                except ValueError as exc:
                    assert str(exc) == error
                else:
                    raise AssertionError("provenance rejection was lost")
            assert (pool, view) == before
            assert len(records) == case_number
            event, record = records[-1]
            assert event == "v5_sor_block"
            assert record["stage"] == "m15_direct_history_first_failure"
            assert record["reason"] == reason and record["timeframe"] == "M15", record
            assert record["generation"] == 17
            assert record["row_index"] == index and record["row_timestamp"] == stamp
            assert record["row_session"] == row_session
            assert record["expected_anchor_minute"] == anchor
            assert record["actual_anchor_minute"] == (
                None if anchor is None else 420 if reason == "SESSION_UNKNOWN" else
                940 if reason == "ANCHOR_MISMATCH" else
                1180 if reason == "COMPLETION_AFTER_ASOF" else 540)
            assert record["frozen_as_of"] == cutoff.isoformat()
            assert record["m15_after_anchor_verified"] is (reason == "ANCHOR_MISMATCH")
            assert record["native_after_history"] is (None if index is None else False)
            assert record["historical_count"] == len(rows)
            assert record["display_count"] == len(view["display_candles"])
            assert "private" not in json.dumps(record)
    assert len(records) == 13


def test_v5_m15_detail_replay_splits_proven_forming_native_tail():
    """Keep REG 15:15 and every completed native row; reject unbound receipts."""
    from types import MethodType

    # Unmodified native M15 rows from existing 000250_AL_15_5900 cache.
    recorded = [
        ("20261105153000", 176600., 176600., 176000., 176300., 2154.),
        ("20261105154500", 176200., 176600., 175700., 176400., 611.),
        ("20261106150000", 171800., 172600., 170100., 170600., 6107.),
        ("20261106151500", 170800., 171200., 170100., 170100., 1617.),
        ("20261106153000", 170100., 170100., 168900., 169000., 4011.),
        ("20261106154500", 168900., 169000., 168000., 168600., 2590.),
        ("20261106180000", 168500., 170700., 168400., 170400., 985.),
        ("20261106181500", 170400., 170900., 170400., 170900., 422.),
    ]
    rows = [dict(zip(("time", "open", "high", "low", "close", "volume"), row))
            for row in recorded]
    stock = flow_module.ValidationStockRef("000250", "offline native M15")
    at = datetime.fromisoformat("2026-11-06T18:18:09+09:00")
    flow_type = flow_module.IndicatorFollowSignalValidationV5Flow
    flow = SimpleNamespace(_verified_sor_tail_provider=None,
                           _session_period_source=lambda _session: (stock.code + "_AL", "INTEGRATED"))
    for name in ("_period_session_scope", "_source_snapshot_datetime",
                 "_direct_selected_completion_time", "_direct_selected_query_valid",
                 "_pool_computation_source_identity", "_sor_m15_after_anchor_verified",
                 "_historical_rows_from_candles"):
        setattr(flow, name, getattr(flow_type, name))
    for name in ("_require_selected_timeframe_pool",):
        setattr(flow, name, MethodType(getattr(flow_type, name), flow))
    native_inputs = []

    def revalidate(session, pool, view, frozen, *args, **kwargs):
        native_inputs.append(deepcopy(view))
        return flow_type._sor_m1_calculation_view(flow, session, pool, view, frozen,
                                               *args, **kwargs)

    flow._sor_m1_calculation_view = revalidate

    class Revalidated(Exception):
        pass

    def stop_before_replay(view):
        raise Revalidated(view)

    with patch.object(flow_module, "required_validation_history_context_bars", return_value=0), \
            patch.object(flow_module, "_trace_v5_historical_boundary"), \
            patch.object(flow_module, "trace_signal_validation_event"):
        for regular_only in (True, False):
            settings = SimpleNamespace(to_dict=lambda: {
                "validation_market_scope": {"regular_market_only": regular_only}})
            session = SimpleNamespace(request=SimpleNamespace(
                stock=stock, settings_snapshot=settings, timeframe_key="M15",
                timeframe_minutes=15))
            for defect in (None, "candle", "request_id", "missing", "snapshot"):
                query = dict(stock_code=stock.code, timeframe_key="M15", timeframe_minutes=15,
                             market_data_identity=stock.code + "_AL", market_source="INTEGRATED",
                             request_id="offline-native-M15", request_as_of=at.isoformat(),
                             last_completed_time=rows[-2]["time"])
                receipt = dict(query, candle=deepcopy(rows[-1]))
                if defect == "candle":
                    receipt["candle"]["close"] += 1
                elif defect == "request_id":
                    receipt["request_id"] = "unbound"
                pool = dict(stock=stock, candles=deepcopy(rows[:-1]),
                            timeframe_key="M15", timeframe_minutes=15,
                            market_data_identity=stock.code + "_AL", market_source="INTEGRATED",
                            request_id=query["request_id"], direct_selected_query=query,
                            selected_direct_forming_snapshot=None if defect == "missing" else receipt)
                frozen = (flow._pool_computation_source_identity(pool), at,
                          {"projection": "DIRECT_COMPLETED_TIMEFRAME"})
                native = dict(pool, candles=deepcopy(rows), display_candles=deepcopy(rows),
                              calculation_source="SELECTED_DIRECT_AL_WITH_DIRECT_CURRENT",
                              _direct_current_calculation_snapshot=frozen)
                prepared = flow._regular_minute_evaluation_view(session, native)
                expected = deepcopy(prepared["candles"])
                if defect == "snapshot":
                    prepared = dict(prepared, _direct_current_calculation_snapshot=None)
                before = deepcopy((pool, prepared))
                native_inputs.clear()
                try:
                    flow_type._prepare_replay_result_from_pool(
                        flow, session, pool, history_target=len(expected),
                        prepared_view_pool=prepared, sor_m1_calculation_snapshot=frozen,
                        expected_sor_m1_calculation=expected, marking_view_check=stop_before_replay)
                except Revalidated as ready:
                    assert defect is None, defect
                    verified = ready.args[0]
                    assert verified["candles"] == expected
                    assert verified["display_candles"] == expected
                    assert native_inputs[-1]["candles"] == rows[:-1]
                    assert native_inputs[-1]["display_candles"] == (
                        rows[2:4] if regular_only else rows[:-1])
                    if regular_only:
                        assert verified["candles"] == rows[2:4]
                        assert verified["candles"][-1]["time"] == "20261106151500"
                        assert verified["_regular_minute_source_view"]["candles"] == rows
                except ValueError as exc:
                    assert defect is not None, str(exc)
                    assert str(exc) == (
                        "SOR_M1_BLOCKED:SOR_DIRECT_CURRENT_PROVENANCE_INVALID"
                        if defect == "request_id" else
                        "SOR_M1_BLOCKED:SOR_DIRECT_HISTORY_PROVENANCE_INVALID")
                else:
                    raise AssertionError("revalidation was skipped")
                assert (pool, prepared) == before
