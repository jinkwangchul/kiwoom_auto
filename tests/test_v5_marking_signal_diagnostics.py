"""Offline shared diagnostics and native publication/drain safety."""

import json
from types import SimpleNamespace

import pytest

import gui_indicator_follow_signal_validation_v5_flow as module
from tests.test_v5_sor_marking_source import marking_case as source_marking_case


def marking_case():
    flow, session, raw, window, request, tooltips = source_marking_case()
    flow._validation_source_by_code[session.request.stock.code] = True
    return flow, session, raw, window, request, tooltips


def events(path):
    module._V5_MARKING_DIAGNOSTIC_QUEUE.join()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
    monkeypatch.setattr(flow, "_source_selection_is_current", lambda _: True)
    monkeypatch.setattr(flow, "_selected_range_binding_identity", lambda _: None)
    flow._v5_native_tr_pending[key] = {object(), object()}

    def records(event):
        assert all(path == module._V5_MARKING_DIAGNOSTIC_PATH for path, _ in queued)
        return [r for _, line in queued if (r := json.loads(line))["event"] == event]

    return SimpleNamespace(flow=flow, pool=pool, window=window, request=request,
        session=session, key=key, queued=queued, records=records, tooltips=tooltips)


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
