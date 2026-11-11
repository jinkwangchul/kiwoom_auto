"""Focused, offline evidence for the V5 marking request lifecycle."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as module
from tests.test_v5_sor_marking_source import InlineThread, marking_case as source_marking_case


def marking_case():
    flow, session, raw, window, request, tooltips = source_marking_case()
    flow._validation_source_by_code[session.request.stock.code] = True
    return flow, session, raw, window, request, tooltips


def events(path):
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_marking_worker_logs_request_and_calculation_counts(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)
    snapshot = SimpleNamespace(
        candle_count=3, _entries=(object(), object()),
        to_candles=lambda: request["calculation"], to_entries=lambda: [],
    )
    monkeypatch.setattr(
        flow, "_prepare_replay_result_from_pool",
        lambda *_args, **_kwargs: SimpleNamespace(snapshot=snapshot),
    )
    monkeypatch.setattr(module, "_build_validation_projection_data", lambda *_args, **_kwargs: ({}, None))

    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    assert payloads[0]["snapshot"] is snapshot
    records = events(path)
    assert [record["event"] for record in records] == ["marking_requested", "marking_calculated"]
    assert records[1]["calculation_count"] == 3
    assert records[1]["display_count"] == 3
    assert records[1]["entry_count"] == 2
    assert [record["timeframe_key"] for record in records] == ["M1", "M1"]
    assert all("settings" not in record and "candles" not in record and "key" not in record for record in records)


def test_marking_success_trace_sanitizes_timeframe_key(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    module._trace_v5_marking("marking_requested", 12, 1, 2, timeframe_key="M5")
    module._trace_v5_marking("marking_calculated", 12, 1, 2,
                             timeframe_key="password=secret")
    records = events(path)
    assert records[0]["timeframe_key"] == "M5"
    assert "timeframe_key" not in records[1]
    assert "secret" not in path.read_text(encoding="utf-8")


def test_marking_failure_uses_safe_reason_and_rejection_is_distinct(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    request["settings"] = SimpleNamespace(to_dict=lambda: {"bar": {"bar_minutes": -1}})
    flow._start_marking(window, request)
    records = events(path)
    assert [record["event"] for record in records] == ["marking_requested", "marking_failed"]
    assert records[-1]["reason"] == "MARKING_TIMEFRAME_MISMATCH"
    assert records[-1]["phase"] == "precheck"
    assert records[-1]["exception_type"] == "ValueError"


def test_marking_worker_stale_traces_exact_current_checks(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    monkeypatch.setattr(module, "signal_validation_trace_enabled", lambda: True)
    trace_records = []
    monkeypatch.setattr(
        module, "trace_signal_validation_event",
        lambda event, **fields: trace_records.append((event, fields)),
    )
    flow, _session, _raw, window, request, _tooltips = marking_case()
    window._marking_key = "superseded-after-request"

    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    assert events(path)[-1]["event"] == "marking_stale"
    stale = [
        fields for event, fields in trace_records
        if event == "v5.marking.worker_stale"
    ]
    assert len(stale) == 1
    assert stale[0]["phase"] == "entry"
    assert stale[0]["checks"]["marking_key"] is False
    assert stale[0]["checks"]["request_generation"] is True
    assert all(type(value) is bool for value in stale[0]["checks"].values())
    assert "superseded-after-request" not in str(stale[0])


def test_marking_completion_logs_published_or_stale(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    window.apply_marking_snapshot = lambda *_args: True
    payload = dict(window_key=id(window), generation=1, key="mark", request_generation=1,
                   snapshot=object(), comparison=object(), error="")

    flow._on_marking_completed(payload)
    window.apply_marking_snapshot = lambda *_args: False
    flow._on_marking_completed(payload)
    window._marking_generation = 2
    flow._on_marking_completed(payload)

    assert [record["event"] for record in events(path)] == [
        "marking_published", "marking_stale", "marking_stale",
    ]


def test_marking_worker_failure_never_logs_exception_text(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()
    payloads = []
    flow.marking_completed.disconnect()
    flow.marking_completed.connect(payloads.append)

    def fail(*_args, **_kwargs):
        raise ValueError("credential=secret; candle rows=private")

    monkeypatch.setattr(flow, "_prepare_replay_result_from_pool", fail)
    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    assert payloads[0]["error"].startswith("credential=secret")
    assert [record["event"] for record in events(path)] == [
        "marking_requested", "marking_failed",
    ]
    assert "secret" not in path.read_text(encoding="utf-8")
    assert events(path)[-1]["reason"] == "UNCLASSIFIED"
    assert events(path)[-1]["phase"] == "worker"
    assert events(path)[-1]["exception_type"] == "ValueError"


def test_marking_worker_logs_known_sor_source_failure(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()

    def fail(*_args, **_kwargs):
        raise ValueError("SOR_M1_BLOCKED:SOURCE_UNAVAILABLE")

    monkeypatch.setattr(flow, "_prepare_replay_result_from_pool", fail)
    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    record = events(path)[-1]
    assert record["event"] == "marking_failed"
    assert record["reason"] == "SOR_M1_BLOCKED:SOURCE_UNAVAILABLE"
    assert record["phase"] == "worker"
    assert record["exception_type"] == "ValueError"


def test_marking_logger_is_bounded_append_only_and_sanitized(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    module._trace_v5_marking("marking_failed", 12, 1, 2, reason="password=secret\ncandles=[]")
    module._trace_v5_marking("marking_requested", 12, 1, 2, calculation_count=999999999)
    records = events(path)
    assert records[0]["reason"] == "UNCLASSIFIED"
    assert "calculation_count" not in records[1]
    assert "secret" not in path.read_text(encoding="utf-8")
    assert all(len(line) < 512 for line in path.read_text(encoding="utf-8").splitlines())

    prior = path.read_bytes()
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_MAX_BYTES", len(prior))
    module._trace_v5_marking("marking_requested", 12, 1, 2)
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    assert path.read_bytes() == prior


def test_worker_reports_native_warmup_shortage_as_bounded_diagnostic(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, request, _tooltips = marking_case()

    def fail(*_args, **_kwargs):
        raise ValueError("MARKING_WARMUP_INSUFFICIENT: missing=860")

    monkeypatch.setattr(flow, "_prepare_replay_result_from_pool", fail)
    with patch.object(module, "Thread", InlineThread):
        flow._start_marking(window, request)

    record = events(path)[-1]
    assert record["event"] == "marking_failed"
    assert record["reason"] == "MARKING_WARMUP_INSUFFICIENT"
    assert record["missing_count"] == 860
    assert record["worker_predicate"] == "WARMUP_INSUFFICIENT"
    assert record["phase"] == "worker"
    assert "missing=860" not in path.read_text(encoding="utf-8")


def test_warmup_shortage_diagnostic_rejects_untrusted_or_invalid_counts(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    for reason in (
        "MARKING_WARMUP_INSUFFICIENT: missing=0",
        "MARKING_WARMUP_INSUFFICIENT: missing=1000001",
        "MARKING_WARMUP_INSUFFICIENT: missing=000860",
        "MARKING_WARMUP_INSUFFICIENT: missing=860 extra=secret",
    ):
        module._trace_v5_marking(
            "marking_failed", 12, 1, 2, reason=reason,
            phase="worker", worker_predicate="UNKNOWN_WORKER_SUBPATH",
        )
    records = events(path)
    assert len(records) == 4
    assert all(record["reason"] == "UNCLASSIFIED" for record in records)
    assert all(record["worker_predicate"] == "UNKNOWN_WORKER_SUBPATH"
               for record in records)
    assert all("missing_count" not in record for record in records)
    assert "secret" not in path.read_text(encoding="utf-8")


def test_marking_success_replaces_only_previous_warmup_notice(tmp_path, monkeypatch):
    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    flow, _session, _raw, window, _request, _tooltips = marking_case()
    displayed = ["마킹 자료 부족: 웜업 860봉 부족"]
    window.validation_status_label = SimpleNamespace(
        text=lambda: displayed[-1], setText=displayed.append,
    )
    window.apply_marking_snapshot = lambda *_args: True
    payload = dict(
        window_key=id(window), generation=1, key="mark", request_generation=1,
        snapshot=object(), comparison=object(), error="",
    )

    flow._on_marking_completed(payload)
    assert displayed[-1] == "마킹 계산 완료"
    displayed.append("외부 검증 상태")
    flow._on_marking_completed(payload)
    assert displayed[-1] == "외부 검증 상태"
    events(path)


def test_marking_other_failure_replaces_previous_warmup_notice():
    flow, _session, _raw, window, _request, tooltips = marking_case()
    displayed = ["마킹 자료 부족: 웜업 900봉 부족"]
    window.validation_status_label = SimpleNamespace(
        text=lambda: displayed[-1], setText=displayed.append,
    )
    payload = dict(
        window_key=id(window), generation=1, key="mark", request_generation=1,
        snapshot=None, comparison=None, error="MARKING_SOURCE_UNAVAILABLE",
    )

    flow._on_marking_completed(payload)
    assert displayed[-1] == "마킹 실패: 마킹 버튼 도움말 확인"
    assert tooltips[-1] == "MARKING_SOURCE_UNAVAILABLE"


def test_timeframe_only_change_does_not_reconfirm_signal_ui():
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    fingerprint = IndicatorFollowSignalValidationV5Window._signal_ui_fingerprint
    initial = {
        "basic": {
            "basic_signal_interval_combo": "3",
            "buy_signal_expr_line": "A",
            "sell_signal_expr_line": "B",
        },
    }
    m1 = {"basic": {**initial["basic"], "basic_signal_interval_combo": "1"}}
    changed_signal = {"basic": {**initial["basic"], "buy_signal_expr_line": "C"}}

    assert fingerprint(m1) == fingerprint(initial)
    assert fingerprint(changed_signal) != fingerprint(initial)


def test_marking_toggle_diagnostic_records_only_safe_routes(tmp_path, monkeypatch):
    from types import MethodType
    from gui_indicator_follow_signal_validation_v5_window import (
        IndicatorFollowSignalValidationV5Window,
    )

    path = tmp_path / "v5_marking_signal.jsonl"
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_PATH", path)
    window = SimpleNamespace(
        _marking_generation=7, _normal_base_timeframe_key="M1",
        _marking_cache=("key", object(), object()),
        _marking_timeframe_pending=None, _pending_result_settings_snapshot=None,
        _marking_source_revision_owner=None,
        _disable_marking=lambda **_kw: None,
    )
    window._trace_marking_toggle = MethodType(
        IndicatorFollowSignalValidationV5Window._trace_marking_toggle, window,
    )
    IndicatorFollowSignalValidationV5Window._on_marking_toggled(window, False)
    window._trace_marking_toggle("cache_reused", checked=True,
                                 cache_hit=True, snapshot_applied=True)
    window._trace_marking_toggle("request_emitted", checked=True,
                                 request_emitted=True)
    module._trace_v5_marking("marking_ui", 12, 1, 2,
                             route="password=private", diagnostic={"secret": "private"})

    records = events(path)
    assert [record["event"] for record in records] == ["marking_ui"] * 4
    assert [record.get("route") for record in records] == [
        "enter_off", "cache_reused", "request_emitted", None,
    ]
    assert records[0]["diagnostic"]["checkbox_on"] is False
    assert records[0]["diagnostic"]["cache_present"] is True
    assert records[1]["diagnostic"]["cache_hit"] is True
    assert records[1]["diagnostic"]["snapshot_applied"] is True
    assert records[2]["diagnostic"]["request_emitted"] is True
    assert "private" not in path.read_text(encoding="utf-8")


@pytest.fixture
def native_decision_case(monkeypatch):
    """Keep the real decision paths and serializer; capture the existing sink in memory."""
    queued = []
    monkeypatch.delenv("KIWOOM_SIGNAL_VALIDATION_TRACE", raising=False)
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_STARTED", True)
    monkeypatch.setattr(module, "_V5_MARKING_DIAGNOSTIC_QUEUE",
                        SimpleNamespace(put_nowait=queued.append))
    flow, _, pool, window, request, tooltips = marking_case()
    settings = module.ValidationSettingsSnapshot(
        {"bar": {"bar_minutes": 3}, "validation_timeframe": {"key": "M3"}})
    session = module.ValidationSession(module.ValidationRequest(window.stock, settings, 3),
                                       operation_active_reader=lambda: False)
    key = id(window)
    flow._validation_sessions[key] = session
    pool.update(timeframe_key="M3", market_source="INTEGRATED",
                market_data_identity=window.stock.code + "_AL")
    request["settings"] = settings
    window._marking_policy_refresh_pending = True
    window._v5_marking_binding_pending = True
    window._prepare_marking_policy_refresh = lambda **_: True
    monkeypatch.setattr(flow, "_source_selection_is_current", lambda _: True)
    monkeypatch.setattr(flow, "_selected_range_binding_identity", lambda _: None)
    flow._v5_native_tr_pending[key] = {object(), object()}

    def records(event):
        assert all(path == module._V5_MARKING_DIAGNOSTIC_PATH for path, _ in queued)
        return [r for _, line in queued if (r := json.loads(line))["event"] == event]

    return SimpleNamespace(flow=flow, pool=pool, window=window, request=request,
        session=session, key=key, queued=queued, records=records, tooltips=tooltips)


@pytest.mark.parametrize("present, same_pool, current_session, current_generation, accepted", [
    (False, False, True, True, False),
    (False, True, False, False, False),
    (True, False, True, True, False),
    (True, False, True, True, True),
])
def test_native_decision_candidate_explains_queue_or_snapshot_wait(
        native_decision_case, monkeypatch, present, same_pool,
        current_session, current_generation, accepted):
    r = native_decision_case
    r.window._marking_policy_refresh_pending = False
    r.window._v5_marking_binding_pending = False
    candidate = r.pool if same_pool else dict(r.pool)
    if present:
        candidate["native_live_snapshot"] = {"account": "PRIVATE_ACCOUNT"}
    pending = (r.session if current_session else object(), candidate,
               1 if current_generation else 2)
    r.flow._native_selected_pending[r.key] = pending
    calls = []
    monkeypatch.setattr(r.flow, "_queue_native_selected",
        lambda *args: calls.append("queue") or accepted)
    monkeypatch.setattr(r.flow, "_start_pending_window_work",
        lambda key: calls.append("drain"))
    r.flow._start_marking(r.window, r.request)
    decisions = r.records("marking_native_candidate")
    assert [d["reason"] for d in decisions] == (
        ["CANDIDATE_READY", "QUEUE_ACCEPTED" if accepted else "QUEUE_REJECTED"]
        if present else ["CANDIDATE_SNAPSHOT_MISSING"])
    state = decisions[0]["diagnostic"]
    assert state["candidate_native_snapshot_present"] is present
    assert state["candidate_is_pool"] is same_pool
    assert state["pending_session_current"] is current_session
    assert state["pending_generation_current"] is current_generation
    assert state["native_query_pending"] is True
    assert state["pending_tr_tokens_count"] == 2
    assert decisions[0]["timeframe_key"] == "M3"
    assert decisions[0]["market_data_identity"] == "005930_AL"
    assert calls == (["queue", "drain"] if accepted else ["queue"] if present else [])
    assert (r.key in r.flow._native_selected_pending) is (not present)
    assert "PRIVATE_ACCOUNT" not in str(r.queued)


@pytest.mark.parametrize("branch, reason, result", [
    ("absent", "PENDING_ABSENT", "NOT_ATTEMPTED"),
    ("window", "WINDOW_MISSING", "REJECTED"),
    ("owner", "OWNER_NOT_CURRENT", "REJECTED"),
    ("receipt", "SNAPSHOT_NOT_CURRENT", "REJECTED"),
    ("active", "PREPARE_ACTIVE", "NOT_ATTEMPTED"),
    ("queued", "PREPARE_PENDING", "NOT_ATTEMPTED"),
    ("ready", "WINDOW_NOT_READY", "NOT_ATTEMPTED"),
    ("history", "HISTORY_DEFICIT", "NOT_ATTEMPTED"),
    ("prepared_receipt", "PREPARED_SNAPSHOT_NOT_CURRENT", "REJECTED"),
    ("waiting", "PUBLISH_WAITING", "WAITING"),
    ("preparing", "PUBLISH_PREPARING", "PREPARING"),
    ("completed", "PUBLISH_PENDING_CLEARED", "PENDING_CLEARED"),
    ("completed_clear", "PUBLISH_PENDING_CLEARED", "PENDING_CLEARED"),
    ("other_prepare", "PUBLISH_WAITING", "WAITING"),
    ("superseded", "PUBLISH_SUPERSEDED", "SUPERSEDED"),
    ("error", "PUBLISH_VALUE_ERROR", "ERROR"),
    ("publish_error", "PUBLISH_VALUE_ERROR", "ERROR"),
])
@pytest.mark.parametrize("logging_failure", [False, True])
def test_native_decision_publisher_reports_existing_outcome_once(
        native_decision_case, monkeypatch, branch, reason, result, logging_failure):
    r = native_decision_case
    if logging_failure:
        def fail_log(*args, **kwargs):
            raise RuntimeError("PRIVATE_LOGGING_FAILURE")
        monkeypatch.setattr(module, "_trace_v5_marking", fail_log)
    pool = dict(r.pool, native_live_snapshot={"request_id": "PRIVATE_RECEIPT"})
    work = pool["_v5_native_work"] = dict(state="WAITING", token=object(),
        owner=dict(window=r.window, base_ready=True, marking_generation=1))
    pending = (r.session, pool, 1)
    if branch != "absent":
        r.flow._native_selected_pending[r.key] = pending
    if branch == "window":
        r.flow._open_windows.pop(r.key)
    if branch == "active":
        r.flow._prepare_active[r.key] = object()
    if branch == "queued":
        r.flow._prepare_pending[r.key] = {}
    checks, published, dropped = [], [], []
    monkeypatch.setattr(r.flow, "_native_selected_owner_is_current",
        lambda *args: checks.append("owner") or branch != "owner")
    monkeypatch.setattr(r.flow, "_live_native_snapshot_is_current",
        lambda *args: checks.append("receipt") or (
            branch != "receipt" and (branch != "prepared_receipt" or len(args) == 5)))
    monkeypatch.setattr(r.flow, "_native_selected_window_ready",
        lambda *args, **kw: checks.append("ready") or branch != "ready")

    def history(*args):
        checks.append("history")
        if branch == "error":
            raise ValueError("PRIVATE_ACCOUNT")
        return {}, 1 if branch == "history" else 0

    def publish(*args, **kw):
        published.append(True)
        if branch == "publish_error":
            raise ValueError("PRIVATE_ACCOUNT")
        if branch == "preparing":
            r.flow._prepare_active[r.key] = work["token"]
        elif branch == "other_prepare":
            r.flow._prepare_active[r.key] = object()
        elif branch == "superseded":
            r.flow._native_selected_pending[r.key] = (r.session, dict(pool), 1)
        elif branch in {"completed", "completed_clear"}:
            r.flow._native_selected_pending.pop(r.key)
            if branch == "completed_clear":
                r.window._marking_policy_refresh_pending = False
                r.window._v5_marking_binding_pending = False

    def drop(key, value, **kw):
        dropped.append(kw.get("invalid", False))
        r.flow._native_selected_pending.pop(key)

    monkeypatch.setattr(r.flow, "_normal_base_history_view", history)
    monkeypatch.setattr(r.flow, "_use_pool_for_window", publish)
    monkeypatch.setattr(r.flow, "_drop_native_selected", drop)
    r.flow._publish_pending_native_selected(r.key)
    records = r.records("marking_native_publisher")
    attempted = branch in {"waiting", "preparing", "completed", "completed_clear", "publish_error",
                           "other_prepare", "superseded"}
    if logging_failure:
        assert records == []
    else:
        assert records[-1]["reason"] == reason
        assert records[-1]["publish_result"] == result
        assert records[-1]["publish_attempted"] is attempted
    assert published == ([True] if attempted else [])
    assert dropped == ([False] if branch in {"window", "owner"} else
                       [True] if branch in {"receipt", "prepared_receipt"} else [])
    assert checks.count("owner") == (0 if branch in {"absent", "window"} else 1)
    assert checks.count("ready") <= 1 and checks.count("history") <= 1
    assert checks.count("receipt") <= 2
    assert "PRIVATE" not in str(r.queued)
    if branch in {"active", "queued", "ready", "history", "waiting", "error"}:
        before = list(r.queued)
        for _ in range(5):
            r.flow._publish_pending_native_selected(r.key)
        assert r.queued == before


def test_native_decision_candidate_refresh_skip_is_distinct(native_decision_case, monkeypatch):
    r = native_decision_case
    r.flow._native_selected_pending[r.key] = (r.session, r.pool, 1)
    r.window._prepare_marking_policy_refresh = lambda **kw: False
    monkeypatch.setattr(r.flow, "_refresh_completed_direct_bar", lambda *args: True)
    r.flow._start_marking(r.window, r.request)
    assert r.records("marking_native_candidate")[-1]["reason"] == "REFRESH_NOT_ACCEPTED"


def test_native_decision_candidate_without_pending_tuple(native_decision_case):
    r = native_decision_case
    r.flow._prepare_active[r.key] = object()
    r.flow._start_marking(r.window, r.request)
    record = r.records("marking_native_candidate")[0]
    assert record["reason"] == "CANDIDATE_SNAPSHOT_MISSING"
    assert record["diagnostic"]["candidate_is_pool"] is True
    assert all(record["diagnostic"][field] is False for field in (
        "native_query_pending", "pending_tuple_present", "pending_session_current",
        "pending_generation_current"))


def test_native_decision_collection_failure_leaves_drain_unchanged(native_decision_case):
    r = native_decision_case
    class UnreadableTokens:
        def __len__(self):
            raise RuntimeError("PRIVATE_ACCOUNT")
    r.flow._v5_native_tr_pending[r.key] = UnreadableTokens()
    active = r.flow._prepare_active[r.key] = object()
    r.flow._start_pending_window_work(r.key)
    assert r.flow._prepare_active[r.key] is active
    assert r.key not in r.flow.__dict__["_native_selected_draining"]
    assert r.queued == []


def test_native_decision_dedup_resets_only_for_new_generation(native_decision_case):
    r = native_decision_case
    r.flow._prepare_active[r.key] = object()
    for _ in range(100):
        r.flow._start_pending_window_work(r.key)
    assert len(r.records("marking_native_drain")) == 1
    r.window._marking_generation += 1
    r.flow._start_pending_window_work(r.key)
    r.flow._request_generation[r.key] += 1
    r.flow._start_pending_window_work(r.key)
    assert len(r.records("marking_native_drain")) == 3
    r.window._marking_policy_refresh_pending = False
    r.window._v5_marking_binding_pending = False
    r.flow._start_pending_window_work(r.key)
    assert len(r.records("marking_native_drain")) == 3


@pytest.mark.parametrize("branch, reason", [
    ("reentrant", "DRAIN_REENTRANT"),
    ("active", "PREPARE_ACTIVE"),
    ("range", "RANGE_REPLAY_ACTIVE"),
    ("tr", "TR_TOKENS_PENDING"),
    ("publisher", "DRAIN_NATIVE_PENDING"),
    ("done", "DRAIN_COMPLETE"),
])
def test_native_decision_drain_keeps_original_wait_and_exception_behavior(
        native_decision_case, monkeypatch, branch, reason):
    r = native_decision_case
    if branch == "reentrant":
        r.flow.__dict__["_native_selected_draining"] = {r.key}
    elif branch == "active":
        r.flow._prepare_active[r.key] = object()
    elif branch == "range":
        r.flow._range_replay_active[r.key] = object()
    elif branch == "tr":
        r.flow._prepare_pending[r.key] = {"run_request": object()}
    elif branch == "publisher":
        r.flow._native_selected_pending[r.key] = (r.session, r.pool, 1)
        monkeypatch.setattr(r.flow, "_publish_pending_native_selected", lambda _: None)
    monkeypatch.setattr(r.flow, "_published_direct_current_snapshot", lambda *args: None)
    r.flow._start_pending_window_work(r.key)
    records = r.records("marking_native_drain")
    assert records[-1]["reason"] == reason
    assert records[-1]["diagnostic"]["pending_tr_tokens_count"] == 2
    before = list(r.queued)
    for _ in range(5):
        r.flow._start_pending_window_work(r.key)
    assert r.queued == before
    assert (r.key in r.flow.__dict__["_native_selected_draining"]) is (branch == "reentrant")


def test_native_decision_logging_failure_cannot_interrupt_drain(native_decision_case, monkeypatch):
    r = native_decision_case
    def fail(*args, **kw):
        raise RuntimeError("PRIVATE_ACCOUNT")
    monkeypatch.setattr(module, "_trace_v5_marking", fail)
    monkeypatch.setattr(r.flow, "_drain_pending_window_work", fail)
    with pytest.raises(RuntimeError, match="PRIVATE_ACCOUNT"):
        r.flow._start_pending_window_work(r.key)
    assert r.key not in r.flow.__dict__["_native_selected_draining"]
    r.flow._prepare_active[r.key] = object()
    module.IndicatorFollowSignalValidationV5Flow._drain_pending_window_work(r.flow, r.key)
    assert r.queued == []


def test_native_decision_serializer_rejects_untrusted_fields(native_decision_case):
    r = native_decision_case
    module._trace_v5_marking("marking_native_candidate", r.key, 1, 1,
        reason="PRIVATE_REASON", phase="PRIVATE_PHASE", publish_result="PRIVATE_RESULT",
        publish_attempted=1, market_data_identity="account=PRIVATE_ACCOUNT",
        diagnostic=dict(candidate_native_snapshot_present="PRIVATE_VALUE", candidate_is_pool=1,
            pending_session_current=True, pending_generation_current=False,
            pending_tr_tokens_count=1_000_001, account="PRIVATE_ACCOUNT"))
    record = r.records("marking_native_candidate")[0]
    assert record["reason"] == "UNCLASSIFIED"
    assert record["diagnostic"] == dict(pending_session_current=True, pending_generation_current=False)
    assert "publish_attempted" not in record and "publish_result" not in record
    assert "phase" not in record and "market_data_identity" not in record
    assert "PRIVATE" not in str(r.queued)
