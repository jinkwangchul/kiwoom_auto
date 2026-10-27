"""Isolated V5 validation fixtures; no legacy validation imports."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog

import gui_indicator_follow_signal_validation_v5_flow as flow_module
from indicator_follow_signal_validation_historical_cache import (
    IndicatorFollowSignalValidationHistoricalCache,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from tests.qt_test_support import ensure_qapplication, flush_deferred_deletes


def v5_rules():
    path = Path(__file__).resolve().parents[1] / "routines" / "지표추종매매" / "rules.json"
    return deepcopy(json.loads(path.read_text(encoding="utf-8")))


class _FakeHost(QObject):
    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(self, stock):
        super().__init__()
        self.stock = stock
        self.started = []
        self.preflighted = []
        self.pickers = []
        self.picker_parents = []

    def preflight_block_reason(self, snapshot):
        self.preflighted.append(snapshot)
        return None

    def create_stock_picker(self, ui_parent=None):
        self.picker_parents.append(ui_parent)
        return self.pickers.pop(0)

    def start(self, snapshot, *, ui_parent=None):
        self.started.append((snapshot, ui_parent))
        session = ValidationSession(
            ValidationRequest(
                self.stock, snapshot, snapshot.to_dict()["bar"]["bar_minutes"],
            ),
            operation_active_reader=lambda: False,
        )
        self.validation_session_ready.emit(session)
        return session


class _FakeWindow(QDialog):
    validation_run_requested = pyqtSignal(object)
    historical_extension_requested = pyqtSignal()
    settings_apply_requested = pyqtSignal(object)
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)
    recent_stock_remove_requested = pyqtSignal(object)

    def __init__(self, stock, seed, parent=None):
        super().__init__(parent)
        self.stock = stock
        self.seed = seed
        self.snapshots = []
        self.errors = []
        self.initial_requests = 0
        self.apply_results = []
        self.historical_candle_count = None
        self.validation_requests = 0
        self.recent_stock_projections = []
        self.stock_metadata_projections = []
        self.entry_commit_count = 0
        self.pool_installs = []
        self.signal_marker_batches = []
        self.discarded_stock_codes = []
        self.preference_stores = []

    def set_validation_preferences_store(self, store):
        self.preference_stores.append(store)

    def set_historical_candle_count(self, count):
        self.historical_candle_count = count

    def set_historical_candle_pool(self, candles, *, chart_candle_count):
        self.pool_installs.append((len(candles), int(chart_candle_count)))

    def set_signal_marker_entries(self, entries):
        self.signal_marker_batches.append(tuple(entries))

    def commit_entry_state(self):
        self.entry_commit_count += 1

    def set_replay_snapshot(self, snapshot):
        self.snapshots.append(snapshot)

    def show_validation_error(self, message):
        self.errors.append(message)

    def request_initial_validation(self):
        self.initial_requests += 1

    def request_validation(self):
        self.validation_requests += 1
        self.validation_run_requested.emit(
            IndicatorFollowSignalValidationRunRequest(
                self.seed.settings_snapshot, self.historical_candle_count,
            )
        )

    def set_validation_stock(self, stock):
        if stock == self.stock:
            return False
        self.stock = stock
        self.snapshots.clear()
        return True

    def discard_validation_stock_if_matches(self, stock_code):
        code = str(stock_code or "").strip()
        if self.stock is None or self.stock.code != code:
            return False
        self.discarded_stock_codes.append(code)
        self.stock = None
        self.snapshots.clear()
        return True

    def set_recent_stocks(self, stocks):
        self.recent_stock_projections.append(tuple(stocks))

    def set_stock_metadata(self, metadata):
        self.stock_metadata_projections.append(metadata)

    def show_settings_apply_result(self, message, *, success):
        self.apply_results.append((message, success))


class _FakePicker:
    def __init__(self, result, selected_stock=None):
        self._result = result
        self.selected_stock = selected_stock

    def exec_(self):
        return self._result


class _MemoryRecentStockStore:
    def __init__(self, stocks=(), metadata=None):
        self._stocks = tuple(stocks)
        self._metadata = dict(metadata or {})
        self.write_count = 0

    @property
    def recent_stocks(self):
        return tuple(self._stocks)

    def metadata_for(self, stock):
        if not isinstance(stock, ValidationStockRef):
            return None
        record = self._metadata.get(stock.code)
        return None if record is None else dict(record)

    def activate(self, stock):
        updated = (stock,) + tuple(
            candidate for candidate in self._stocks if candidate.code != stock.code
        )
        updated = updated[:20]
        if updated == self._stocks:
            return False
        self._stocks = updated
        self.write_count += 1
        return True

    def remove(self, stock):
        code = stock.code if isinstance(stock, ValidationStockRef) else str(stock or "")
        updated = tuple(candidate for candidate in self._stocks if candidate.code != code)
        if updated == self._stocks:
            return False
        self._stocks = updated
        self.write_count += 1
        return True


class V5ValidationCase:
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_qapplication()
        cls.rules = v5_rules()
        cls.ui_state = deepcopy(cls.rules["indicator_follow_ui_state"]["state"])
        cls.ui_state["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"] = "-"
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()
        self.history_cache_temporary = tempfile.TemporaryDirectory()
        self.default_history_cache = IndicatorFollowSignalValidationHistoricalCache(
            self.history_cache_temporary.name
        )
        self.history_cache_patch = patch.object(
            flow_module,
            "IndicatorFollowSignalValidationHistoricalCache",
            return_value=self.default_history_cache,
        )
        self.history_cache_patch.start()

    def tearDown(self):
        for widget in self.widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        flush_deferred_deletes(self.app)
        self.history_cache_patch.stop()
        self.history_cache_temporary.cleanup()
        flow_module._SHARED_SIGNAL_VALIDATION_HISTORICAL_POOLS.clear()

    def _seed(self, rules=None, ui_state=None):
        resolved_state = deepcopy(ui_state or self.ui_state)
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = resolved_state["sell_ui"]["signal_conditions"][group_name]
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(rules or self.rules), resolved_state,
        )
