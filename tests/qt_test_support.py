from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication, QWidget


QCORE_ONLY_TEST_MODULES = frozenset(
    {
        "tests.test_bar_commit_fast_path",
        "tests.test_kiwoom_realtime_registration",
        "tests.test_market_data_host_separation",
        "tests.test_phase12_load_reconnect_simulation",
        "tests.test_price_signal_observation_gate",
        "tests.test_realtime_primary_reconciliation",
        "tests.test_realtime_shadow_operation_host",
    }
)


class QtApplicationTypeConflict(RuntimeError):
    pass


_QAPPLICATION_REFERENCE: QApplication | None = None
_WidgetT = TypeVar("_WidgetT", bound=QWidget)


def ensure_qapplication(arguments: Sequence[str] | None = None) -> QApplication:
    global _QAPPLICATION_REFERENCE

    application = QCoreApplication.instance()
    if application is not None and not isinstance(application, QApplication):
        raise QtApplicationTypeConflict(
            "A QCoreApplication already owns this process; run QWidget tests "
            "in a separate QApplication process."
        )
    if application is None:
        application = QApplication(list(arguments or ()))
    _QAPPLICATION_REFERENCE = application
    return application


def create_qt_widget_shell(
    widget_type: type[_WidgetT],
    qt_base_type: type[QWidget],
) -> _WidgetT:
    ensure_qapplication()
    widget = widget_type.__new__(widget_type)
    qt_base_type.__init__(widget)
    return widget


def flush_deferred_deletes(
    application: QCoreApplication | None = None,
) -> None:
    current = application or QCoreApplication.instance()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if current is not None:
        current.processEvents()


def dispose_qt_widget(widget: QWidget, *, close: bool = False) -> None:
    if sip.isdeleted(widget):
        return
    if close:
        widget.close()
    widget.deleteLater()
    flush_deferred_deletes()
