"""Offline real selection/replay chain; recorded candles are not broker evidence."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PyQt5.QtCore import QEvent, QPoint, QPointF, QSignalBlocker, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from tests.test_v5_async_marking_range_interleaving_20261031 import no_external_effects
from tests.test_v5_marking_bind_stage2_20261030 import NATIVE_ROWS
from tests.test_indicator_follow_signal_validation_v5_baseline import case, window_for
from tests.test_v5_direct_forming_tail_20261026 import _flow
from tests.test_display_only_integrated_history import _frozen_source_plan
from tests.v5_validation_test_support import _FakeWindow
import gui_indicator_follow_signal_validation_v5_flow as v5


@pytest.fixture
def view(case, monkeypatch):
    window = window_for(case)
    with QSignalBlocker(window.basic_signal_interval_combo):
        window.basic_signal_interval_combo.setCurrentText("1")
    rows = deepcopy(NATIVE_ROWS["M1"])
    rules = window._marking_current_settings().to_dict()
    # Literal close conditions isolate the settings editor, not replay/evaluation.
    rules["bar"] = dict(bar_minutes=1, buy_delay_bar=0, sell_delay_bar=0)
    rules["buy"] = dict(enabled=True, delay_bar=0, groups=[dict(enabled=True,
        conditions=[dict(enabled=True, target="CLOSE", operator=">=", value=228000),
                    dict(enabled=True, target="CLOSE", operator="<=", value=228500)])])
    rules["sell"] = dict(enabled=True, delay_bar=0, signal_logic="OR", signals={
        "macd_sell": dict(enabled=False),
        "profit_rate_sell": dict(enabled=False),
        "ui_condition_c_macd_sell": dict(enabled=True, order_delay_bars=0,
            groups=[dict(enabled=True, conditions=[
                dict(enabled=True, target="CLOSE", operator=">=", value=229000)])])})
    rules.pop("validation_execution", None)
    settings = v5.ValidationSettingsSnapshot(rules)
    monkeypatch.setattr(window, "_marking_current_settings", lambda: settings)
    window._confirmed_signal_ui_fingerprint = window._current_signal_ui_fingerprint()
    window.set_historical_candle_pool(rows, chart_candle_count=len(rows))
    window.install_normal_base(settings, "M1")
    flow = _flow()
    flow._operation_active_reader = lambda: False
    now = datetime.fromisoformat("2026-10-30T11:45:00+09:00")
    flow._now_factory = lambda: now
    key = id(window)
    session = v5.ValidationSession(v5.ValidationRequest(window.stock, settings, 1),
        operation_active_reader=lambda: False)
    session.source_selection = v5._V5SourceSelection(
        window.stock, "M1", key, 7, "FULL", window.stock.code + "_AL",
        "INTEGRATED", True, now)
    historical = v5.ValidationHistoricalSnapshot(stock=window.stock,
        timeframe_minutes=1, timeframe_key="M1", requested_count=5,
        request_id="recorded-M1-range", rows=flow._historical_rows_from_candles(rows),
        market_data_identity=window.stock.code + "_AL", market_source="INTEGRATED",
        request_as_of="2026-10-29T11:45:30+09:00")
    completed, forming, query = flow._direct_selected_history_parts(historical)
    pool = flow._pool_from_candles(session, completed, requested_count=5,
        request_id=historical.request_id, market_data_identity=historical.market_data_identity,
        market_source="INTEGRATED", direct_selected_query=query,
        selected_direct_forming_snapshot=forming)
    flow._open_windows[key] = window
    flow._request_generation[key] = 7
    flow._validation_sessions[key] = session
    flow._historical_pools[key] = pool
    flow._history_targets[key] = 5
    flow._validation_source_by_code[window.stock.code] = True
    flow._pool_dispatch_snapshot(pool)
    publication = pool["_dispatch_snapshot"]
    window._v5_marking_display_publication = (pool, session, 7, publication)
    assert flow._source_selection_is_current(session.source_selection)
    assert flow._marking_publication_is_current(window, window._v5_marking_display_publication)
    assert flow._native_selected_window_ready(window, key)
    jobs, payloads = [], []
    monkeypatch.setattr(v5, "Thread", lambda **kw:
        SimpleNamespace(start=lambda: jobs.append(kw["target"])))
    monkeypatch.setattr(flow, "_emit_range_replay_completed", payloads.append)
    monkeypatch.setattr(flow, "_historical_provider_factory",
        lambda *_a, **_kw: pytest.fail("offline range must not query broker"))
    window.validation_range_changed.connect(
        lambda selected: flow._invalidate_window_range(window, selected))
    window.validation_range_evaluation_requested.connect(
        lambda start, end: flow._run_cached_range_replay(window, start, end))

    def drag(start=1, end=4):
        canvas = window.canvas
        canvas.resize(800, 360)
        left = QPoint(round(canvas._x_for_index(start)), 250)
        right = QPoint(round(canvas._x_for_index(end)), 250)
        assert canvas._nearest_candle_index(left.x()) == start, (left, canvas.width(), canvas._visible_start_index, canvas.pixels_per_candle)
        QTest.mouseDClick(canvas, Qt.LeftButton, pos=left)
        assert canvas._validation_range_drag_start_index == start, (canvas._price_legend_target_at(left.x(), left.y()), canvas._lower_boundary_at(left.y()))
        QApplication.sendEvent(canvas, QMouseEvent(QEvent.MouseMove,
            QPointF(right), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=right)
        assert window.validation_range == (start, end), (getattr(window, "_signal_ui_confirmation_error_detail", None), canvas._validation_range_drag_start_index, left, right)

    def calculate():
        assert len(jobs) == 1
        with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
            jobs.pop()()
        assert len(payloads) == 1
        payload = payloads.pop()
        assert not payload["error"], payload
        return payload

    return SimpleNamespace(window=window, rows=rows, settings=settings,
        flow=flow, key=key, session=session, pool=pool,
        jobs=jobs, payloads=payloads, drag=drag, calculate=calculate)


def test_range_drag_runs_replay_without_marking_control(view):
    r = view
    r.drag()
    payload = r.calculate()
    r.flow._on_range_replay_completed(payload)
    w = r.window
    assert {(m["evaluation_index"], m["side"]) for m in w._chart_marker_records()} == {
        (1, "BUY"), (2, "BUY"), (3, "SELL"), (4, "BUY")}
    cycle, = w.completed_cycles
    assert cycle.buy_indexes == (1, 2)
    assert cycle.average_buy_price == 228250
    assert cycle.sell_price == 229000
    assert cycle.estimated_return_percent == pytest.approx(750 / 228250 * 100)
    assert w.completed_cycle_table.rowCount() == 1
    assert w.canvas._completed_cycle_average_lines
    assert "+1,500" in w.estimated_return_label.text()
    assert "456,500" in w.estimated_return_label.text()
    assert not hasattr(w, "marking_display_check")
    assert not hasattr(w, "marking_evaluation_requested")
    # Starting later cannot inherit the buy at index 1.
    r.drag(2, 4)
    assert w._chart_marker_records() == []
    r.flow._on_range_replay_completed(r.calculate())
    assert w.completed_cycles[0].buy_indexes == (2,)
    assert w.completed_cycles[0].average_buy_price == 228500


@pytest.mark.parametrize("liquidation", [False, True])
def test_regular_market_dummy_preserves_range_and_pending_editor(view, liquidation):
    r = view
    w = r.window
    dummy = w.validation_regular_market_dummy_check
    w.validation_range_liquidation_check.setChecked(liquidation)
    w.resize(2200, 960)
    w.show()
    w.activateWindow()
    QApplication.processEvents()
    w._confirmed_signal_ui_fingerprint = w._current_signal_ui_fingerprint()
    r.drag()
    r.flow._on_range_replay_completed(r.calculate())
    for _ in range(100):
        QApplication.processEvents()
        if (w._visualization_projection_active is None
                and w._visualization_projection_pending is None):
            break
        QTest.qWait(10)
    assert w._visualization_projection_active is None
    assert w._visualization_projection_pending is None
    save = Mock()
    w._validation_preferences_store = SimpleNamespace(save=save)
    signals = [QSignalSpy(signal) for signal in (
        w.validation_run_requested, w.validation_range_changed,
        w.validation_range_evaluation_requested, w.historical_extension_requested,
        w.settings_apply_requested, w.chart_input_invalidated)]
    publication = w._v5_marking_display_publication

    def state():
        return deepcopy((w.collect_indicator_follow_ui_state(),
            w._validation_preferences_snapshot(), w._current_signal_ui_fingerprint(),
            w._confirmed_signal_ui_fingerprint, w.validation_range,
            w.canvas.validation_range, w._chart_marker_records(), w.completed_cycles,
            w.completed_cycle_table.rowCount(), w.estimated_return_label.text(),
            w._range_replay_entries_available, w._candles, w._calculation_candles,
            w.visualization_descriptors, w.visualization_cache.series,
            r.flow._request_generation, r.flow._range_replay_generation,
            r.flow._range_replay_pending, w._pending_validation_ui_fingerprint,
            w._validated_ui_fingerprint, w.basic_signal_interval_combo.currentText()))

    before = state()
    assert before[0]["validation_market_scope"]["regular_market_only"] is False
    assert dummy.focusPolicy() == Qt.TabFocus
    previous = dummy.previousInFocusChain()
    while not (previous.isVisible() and previous.isEnabled()
               and previous.focusPolicy() & Qt.TabFocus):
        previous = previous.previousInFocusChain()
        assert previous is not dummy
    previous.setFocus()
    QApplication.processEvents()
    QTest.keyClick(previous, Qt.Key_Tab)
    assert QApplication.focusWidget() is dummy
    for checked in (True, False):
        QTest.keyClick(dummy, Qt.Key_Space)
        QApplication.processEvents()
        assert dummy.isChecked() is checked
        assert state() == before
    QTest.keyClick(dummy, Qt.Key_Backtab, Qt.ShiftModifier)
    assert QApplication.focusWidget() is previous
    assert state() == before
    editor = w.buy_signal_expr_line
    editor.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is editor
    editor.setText(editor.text() + " ")
    before = state()
    assert before[2] != before[3]  # A real unconfirmed editor draft.
    with patch.object(w, "_queue_signal_validation_ui_confirmation") as queued:
        for checked in (True, False):
            QTest.mouseClick(dummy, Qt.LeftButton)
            QApplication.processEvents()
            assert dummy.isChecked() is checked
            assert QApplication.focusWidget() is editor
            assert state() == before
        queued.assert_not_called()
    assert w._v5_marking_display_publication is publication
    assert r.flow._marking_publication_is_current(w, publication)
    assert r.jobs == r.payloads == []
    assert all(len(spy) == 0 for spy in signals)
    save.assert_not_called()
    # Tab-away is an editor confirmation trigger, separate from dummy toggles.
    with patch.object(w, "_queue_signal_validation_ui_confirmation") as queued:
        QTest.keyClick(editor, Qt.Key_Tab)
        QApplication.processEvents()
        assert QApplication.focusWidget() is not editor
        queued.assert_called_with()


@pytest.mark.parametrize("liquidation", [False, True])
def test_range_clear_removes_virtual_results_preserves_chart(view, liquidation):
    r = view
    w = r.window
    w.validation_range_liquidation_check.setChecked(liquidation)
    r.drag()
    r.flow._on_range_replay_completed(r.calculate())
    assert len(w.completed_cycles) == (2 if liquidation else 1)
    assert bool(w.completed_cycles[-1].is_liquidation) is liquidation
    before = (deepcopy(w._candles), deepcopy(w._calculation_candles),
        w.visualization_descriptors,
        tuple(s for s in w.visualization_cache.series if s[1] != "AVG_PRICE"),
        w.filter_display_check.isChecked())
    # Complete real work but hold its GUI callback across clear.
    r.drag()
    late = r.calculate()
    w._clear_validation_range_interaction()
    r.flow._on_range_replay_completed(late)
    assert w.validation_range is None
    assert not w._range_replay_entries_available
    assert w._chart_marker_records() == []
    assert w.completed_cycles == ()
    assert w.completed_cycle_table.rowCount() == 0
    assert not w.canvas._completed_cycle_average_lines
    assert w.estimated_return_label.text() == ""
    assert (w._candles, w._calculation_candles, w.visualization_descriptors,
        tuple(s for s in w.visualization_cache.series if s[1] != "AVG_PRICE"),
        w.filter_display_check.isChecked()) == before
    assert w.discard_validation_stock_if_matches(w.stock.code)
    w._refresh_validation_range_results()
    assert w.completed_cycle_table.rowCount() == 0


@pytest.mark.parametrize("stale", ["token", "range_generation", "request_generation",
    "pool", "session", "source_selection", "stock", "timeframe", "settings", "mutation"])
def test_range_rejects_cancelled_or_changed_source(view, monkeypatch, stale):
    r = view
    r.drag()
    payload = r.calculate()
    if stale == "token":
        payload["range_token"] = object()
    elif stale == "range_generation":
        r.flow._range_replay_generation[r.key] += 1
    elif stale == "request_generation":
        r.flow._request_generation[r.key] += 1
    elif stale == "pool":
        r.flow._historical_pools[r.key] = dict(r.pool)
    elif stale == "session":
        r.flow._validation_sessions[r.key] = object()
    elif stale == "source_selection":
        r.session.source_selection = v5._V5SourceSelection(
            r.window.stock, "M1", r.key, 6, "FULL", r.window.stock.code + "_AL",
            "INTEGRATED", True, r.flow._now_factory())
    elif stale == "stock":
        r.window._signal_validation_stock = v5.ValidationStockRef("000660", "changed")
    elif stale == "timeframe":
        with QSignalBlocker(r.window.basic_signal_interval_combo):
            r.window.basic_signal_interval_combo.setCurrentText("5")
        r.window._on_validation_timeframe_changed()
    elif stale == "settings":
        changed = r.settings.to_dict()
        changed["buy"]["enabled"] = False
        monkeypatch.setattr(r.window, "_marking_current_settings",
            lambda: v5.ValidationSettingsSnapshot(changed))
    else:
        r.pool["candles"][1]["close"] += 1
    r.flow._on_range_replay_completed(payload)
    assert not r.window._range_replay_entries_available
    assert r.window._chart_marker_records() == []
    assert r.window.completed_cycles == ()


@pytest.mark.parametrize("outcome", ["success", "install_error", "rejected", "stale_error"])
def test_range_waits_for_publication_then_drains_once(view, monkeypatch, outcome):
    r = view
    prepared = []
    previous_chart = deepcopy(r.window._candles)
    previous_binding = r.window._v5_marking_display_publication
    candidate = dict(r.pool)
    candidate.pop("_dispatch_snapshot", None)
    monkeypatch.setattr(r.flow, "_emit_signal_scan_completed", prepared.append)
    r.flow._use_pool_for_window(r.window, r.session, candidate,
        evaluation_count=5, _normal_view=candidate)
    assert r.key in r.flow._prepare_active and len(r.jobs) == 1
    chart_job = r.jobs.pop()
    r.window._v5_marking_binding_pending = True
    r.drag()
    assert r.jobs == []
    assert r.key in r.flow._range_replay_pending
    # Only normal accepted publication releases this common flag.
    r.window._disable_marking(clear_cache=True)
    assert r.window._v5_marking_binding_pending is True
    with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
        chart_job()
    chart, = prepared
    assert chart["normal_base"] and not chart.get("error"), chart
    assert chart["dispatch_publication"] is candidate["_dispatch_snapshot"]
    prepared.clear()
    errors = []
    r.flow.validation_failed.connect(errors.append)
    newer = []
    with monkeypatch.context() as failing:
        if outcome in {"install_error", "stale_error"}:
            def fail_install(*_args, **_kwargs):
                if outcome == "stale_error":
                    r.flow._request_generation[r.key] += 1
                    session = v5.ValidationSession(
                        v5.ValidationRequest(r.window.stock, r.settings, 1),
                        operation_active_reader=lambda: False)
                    session.source_selection = v5._V5SourceSelection(
                        r.window.stock, "M1", r.key, 8, "FULL",
                        r.window.stock.code + "_AL", "INTEGRATED", True,
                        r.flow._now_factory())
                    pool = dict(candidate)
                    pool.pop("_dispatch_snapshot", None)
                    r.flow._use_pool_for_window(r.window, session, pool,
                        evaluation_count=5, _normal_view=pool)
                    newer.append(r.flow._prepare_pending[r.key])
                    r.drag(2, 4)
                raise ValueError("offline normal installation failure")
            failing.setattr(r.window, "install_normal_base", fail_install)
        elif outcome == "rejected":
            apply_pool = r.flow._apply_pool_to_window
            def reject_source(*args, **kwargs):
                r.session.source_selection = v5._V5SourceSelection(
                    r.window.stock, "M1", r.key, 6, "FULL",
                    r.window.stock.code + "_AL", "INTEGRATED", True,
                    r.flow._now_factory())
                return apply_pool(*args, **kwargs)
            failing.setattr(r.flow, "_apply_pool_to_window", reject_source)
        r.flow._on_signal_scan_completed(chart)
    if outcome != "success":
        assert r.window._candles == previous_chart
        assert r.window.chart_stack.currentWidget() is r.window.chart_view
        assert r.window._v5_marking_display_publication is previous_binding
        assert not r.flow._marking_publication_is_current(r.window, previous_binding)
        assert not r.window._range_replay_entries_available
        if outcome == "stale_error":
            # The old failure must not report over or retire the newer publisher.
            assert errors == []
            assert r.window._v5_marking_binding_pending is True
            assert r.flow._prepare_active[r.key] is newer[0]["token"]
            assert r.flow._historical_pools[r.key] is newer[0]["pool"]
            assert r.window.validation_range == (2, 4)
            r.flow._on_signal_scan_completed(dict(chart, error="late old failure"))
            assert r.flow._prepare_active[r.key] is newer[0]["token"]
            assert r.window._v5_marking_binding_pending is True
            assert errors == []
        else:
            assert r.window._v5_marking_binding_pending is False
            assert r.key not in r.flow._range_replay_pending
            assert r.key not in r.flow._prepare_active
            assert r.jobs == []  # Never replay the failed, unbound publication.
            assert errors and "NORMAL_BASE_VIEW_ERROR" in errors[-1]
            r.session.source_selection = v5._V5SourceSelection(
                r.window.stock, "M1", r.key, 7, "FULL",
                r.window.stock.code + "_AL", "INTEGRATED", True,
                r.flow._now_factory())
            r.flow._use_pool_for_window(r.window, r.session, candidate,
                evaluation_count=5, _normal_view=candidate)
        assert len(r.jobs) == 1
        with _frozen_source_plan() as root, patch("routine_signal_probe.PROJECT_ROOT", root):
            r.jobs.pop()()
        retry, = prepared
        assert not retry.get("error"), retry
        r.flow._on_signal_scan_completed(retry)
        # A queued old failure cannot release or discard a later active publisher.
        if outcome == "stale_error":
            assert r.window.validation_range == (2, 4)
    assert r.window._v5_marking_binding_pending is False
    assert r.flow._marking_publication_is_current(
        r.window, r.window._v5_marking_display_publication)
    r.flow._start_pending_window_work(r.key)
    r.flow._start_pending_window_work(r.key)
    assert len(r.jobs) == 1
    r.flow._on_range_replay_completed(r.calculate())
    assert r.window._range_replay_entries_available
    r.flow._start_pending_window_work(r.key)
    assert r.jobs == [] and r.key not in r.flow._range_replay_pending


@pytest.mark.parametrize("error", ["offline range failure", "TR_BUDGET_EXCEEDED"])
def test_range_failure_drains_pending_work_without_releasing_transition_lock(
        view, monkeypatch, error):
    r = view
    r.drag()
    payload = r.calculate()
    payload["error"] = error
    request = v5.IndicatorFollowSignalValidationRunRequest(r.settings, 5)
    r.window._begin_timeframe_transition_lock(request)
    target = r.window._timeframe_transition_target
    drained = []
    drain = r.flow._start_pending_window_work
    def observe(key):
        drained.append(key)
        drain(key)
    monkeypatch.setattr(r.flow, "_start_pending_window_work", observe)
    r.flow._on_range_replay_completed(payload)
    assert r.window._timeframe_transition_target == target
    assert not r.window._timeframe_transition_overlay.isHidden()
    assert drained == [r.key]
    assert error in r.window.validation_status_label.text()
    assert r.window._validation_budget_blocked == (error == "TR_BUDGET_EXCEEDED")
    assert (r.key in r.flow._v5_manual_retry_required) == (error == "TR_BUDGET_EXCEEDED")


@pytest.mark.parametrize("failure", ["worker", "snapshot", "api", "apply"])
def test_current_range_failure_reports_error_and_allows_manual_retry(
        view, monkeypatch, failure):
    r = view
    r.drag()
    payload = r.calculate()
    errors = []
    r.flow.validation_failed.connect(errors.append)
    with monkeypatch.context() as isolated:
        if failure == "worker":
            payload["error"] = "offline range worker failure"
        elif failure == "snapshot":
            payload["snapshot"] = None
        elif failure == "api":
            isolated.setattr(r.window, "apply_range_replay_snapshot_async", None)
            isolated.setattr(r.window, "apply_range_replay_snapshot", None)
        else:
            def fail_apply(*_args, **_kwargs):
                raise ValueError("offline range view failure")
            isolated.setattr(r.window, "apply_range_replay_snapshot_async", fail_apply)
        r.flow._on_range_replay_completed(payload)
    assert errors and errors[-1] in r.window.validation_status_label.text()
    assert r.key not in r.flow._range_replay_active
    assert not r.window._range_replay_entries_available
    assert r.window._candles == r.rows
    r.drag()
    r.flow._on_range_replay_completed(r.calculate())
    assert r.window._range_replay_entries_available
    assert r.window.completed_cycles[0].average_buy_price == 228250


def test_ordinary_range_error_does_not_clear_existing_budget_block(view):
    r = view
    r.drag()
    payload = r.calculate()
    payload["error"] = "offline range failure"
    r.window._validation_budget_blocked = True
    r.flow._on_range_replay_completed(payload)
    assert r.window._validation_budget_blocked


def test_range_failure_supports_existing_message_only_window_callback(view, case):
    r = view
    r.drag()
    payload = r.calculate()
    payload["error"] = "offline range failure"
    fake = _FakeWindow(r.window.stock, r.window._signal_validation_seed)
    case.widgets.append(fake)
    fake.validation_range = r.window.validation_range
    fake._validation_range_times = r.window._validation_range_times
    # This callback contract predates publication-aware windows.
    payload.pop("selected_range_binding")
    r.flow._open_windows[r.key] = fake
    errors = []
    r.flow.validation_failed.connect(errors.append)
    r.flow._on_range_replay_completed(payload)
    assert fake.errors == errors == ["REPLAY_ERROR: offline range failure"]
    assert r.key not in r.flow._range_replay_active
