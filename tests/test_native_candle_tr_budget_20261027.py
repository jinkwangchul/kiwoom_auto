"""Offline checks for the shared opt10080 dispatch admission limit."""

from collections import deque

import kiwoom_api as api_module
from PyQt5.QtCore import QCoreApplication, QObject


class Control:
    def __init__(self):
        self.calls = []

    def dynamicCall(self, signature, *args):
        if signature.startswith("CommRqData"):
            self.calls.append((args[0], args[1]))
            return -1  # A failed broker dispatch still consumes the budget.
        return 0


def _api(monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    api = api_module.KiwoomApi.__new__(api_module.KiwoomApi)
    QObject.__init__(api)
    api._control = Control()
    api._pending_tr = {}
    api._pending_tr_matches_current_session = lambda pending: True
    api._schedule_tr_governor_dispatch = lambda delay: None
    clock = [10_000.0]
    monkeypatch.setattr(api_module, "monotonic", lambda: clock[0])
    return api, clock


def _submit(api, name, trcode, failures):
    pending = {}
    api._pending_tr[name] = pending

    def failed(result, reason):
        api._pending_tr.pop(name)
        failures.append((name, reason))
        return {"ok": False, "status": "REQUEST_FAILED", "error": reason}

    return api._submit_governed_tr_request(
        rqname=name, trcode=trcode, prev_next=0, screen_no="3000",
        inputs=(), pending=pending, on_failed=failed)


def test_candle_rolling_minute_counts_failed_attempts_and_releases_rejection(monkeypatch):
    api, clock = _api(monkeypatch)
    api._ensure_tr_governor_state()
    api._candle_tr_dispatch_history.extend(clock[0] - 39 + i for i in range(39))
    failures = []
    _submit(api, "fortieth", "opt10080", failures)
    assert api._control.calls == [("fortieth", "opt10080")]
    clock[0] += 1
    rejected = _submit(api, "forty-first", "opt10080", failures)
    assert rejected["ok"] is False
    assert failures[-1] == ("forty-first", "CANDLE_TR_BUDGET_EXCEEDED")
    assert "forty-first" not in api._pending_tr
    assert len(api._tr_request_queue) == 0
    assert api._control.calls == [("fortieth", "opt10080")]


def test_candle_rolling_hour_exhaustion_does_not_starve_other_tr(monkeypatch):
    api, clock = _api(monkeypatch)
    api._ensure_tr_governor_state()
    api._candle_tr_dispatch_history = deque(clock[0] - 3598 + i * 6
                                            for i in range(599))
    failures = []
    _submit(api, "six-hundredth", "opt10080", failures)
    clock[0] += 1
    api._tr_governor_dispatching = True
    _submit(api, "blocked", "opt10080", failures)
    _submit(api, "account", "opw00018", failures)
    api._tr_governor_dispatching = False
    api._drain_tr_governor()
    assert ("blocked", "CANDLE_TR_BUDGET_EXCEEDED") in failures
    assert "blocked" not in api._pending_tr
    assert api._control.calls == [("six-hundredth", "opt10080"),
                                  ("account", "opw00018")]
