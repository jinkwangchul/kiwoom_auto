"""An exact wheel-driven prepend chains its next request without another idle delay."""
from __future__ import annotations

import pytest
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from tests import test_indicator_follow_signal_validation_v5_baseline as baseline
from tests.test_indicator_follow_signal_validation_v5_wheel_history_boundary import wheel


@pytest.fixture
def case():
    yield from baseline.case.__wrapped__()


def _window(case):
    window = baseline.window_for(case)
    baseline.install(window, baseline.candles(500))
    window._set_time_view(0, 500)
    return window


def _prepend(window, count, *, timeframe="M1", changed=False):
    rows = baseline.candles(count)
    if changed:
        rows[-1] = dict(rows[-1], close=rows[-1]["close"] + 1)
    window.set_historical_candle_pool(rows, chart_candle_count=count)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, timeframe)


def test_valid_burst_chains_once_after_first_delayed_request(case):
    window = _window(case)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    for _ in range(13):
        wheel(window.canvas, -120)
    QApplication.processEvents()
    assert requests == []
    QTest.qWait(400)
    assert requests == [1]
    _prepend(window, 1000)
    assert requests == [1]
    QApplication.processEvents()
    assert requests == [1, 1]
    QApplication.processEvents()
    assert requests == [1, 1]
    _prepend(window, 1500)
    QApplication.processEvents()
    assert requests == [1, 1]
    assert window._visible_candle_span == pytest.approx(500 * 1.08 ** 13)


@pytest.mark.parametrize("cancel", ["reverse", "scroll", "timeframe", "changed"])
def test_invalidated_burst_does_not_chain(case, cancel):
    window = _window(case)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    for _ in range(13):
        wheel(window.canvas, -120)
    QTest.qWait(400)
    assert requests == [1]
    if cancel == "reverse":
        wheel(window.canvas, 120)
    elif cancel == "scroll":
        window._begin_time_navigation()
    _prepend(window, 1000, timeframe="M5" if cancel == "timeframe" else "M1",
             changed=cancel == "changed")
    QApplication.processEvents()
    assert requests == [1]


def test_cap_and_exhausted_pool_do_not_chain(case):
    window = _window(case)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    for _ in range(30):
        wheel(window.canvas, -120)
    QTest.qWait(400)
    assert requests == [1]
    for count in (1000, 1500, 2000, 2500, 3000, 3500):
        _prepend(window, count)
        QApplication.processEvents()
    assert window._visible_candle_span == 3500
    assert len(requests) == 6
    QApplication.processEvents()
    assert len(requests) == 6


def test_exhausted_calculation_pool_does_not_chain(case):
    window = _window(case)
    requests = []
    window.historical_extension_requested.connect(lambda: requests.append(1))
    for _ in range(13):
        wheel(window.canvas, -120)
    QTest.qWait(400)
    assert requests == [1]
    window.set_historical_candle_pool(baseline.candles(5000), chart_candle_count=1000)
    window.install_normal_base(window._signal_validation_seed.settings_snapshot, "M1")
    QApplication.processEvents()
    assert requests == [1]
