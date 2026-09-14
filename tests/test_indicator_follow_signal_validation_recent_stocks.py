# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QSettings, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QComboBox

from gui_indicator_follow_signal_validation_flow import (
    IndicatorFollowSignalValidationFlow,
)
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationStockDisplay,
)
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
)
from indicator_follow_signal_validation_recent_stocks import (
    MAX_RECENT_STOCKS,
    RECENT_STOCKS_SETTINGS_KEY,
    IndicatorFollowSignalValidationRecentStockStore,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef


class _FakeSettings:
    def __init__(self, value=""):
        self.stored_value = value
        self.set_calls = []
        self.sync_calls = 0

    def value(self, key, default=""):
        return self.stored_value if key == RECENT_STOCKS_SETTINGS_KEY else default

    def setValue(self, key, value):
        self.set_calls.append((key, value))
        self.stored_value = value

    def sync(self):
        self.sync_calls += 1


class _ConnectedBroker:
    def is_connected(self):
        return True


def _record(index: int, *, name: str | None = None) -> dict[str, object]:
    code = f"{index:06d}"
    return {
        "code": code,
        "name": name or f"종목{index}",
        "market": "KOSPI",
        "classification": "일반종목",
        "nxt_available": True,
        "status": "정상",
        "master_stock_state": "정상",
        "master_construction": "정상",
    }


def _loader(records):
    snapshot = StockLibraryLoadSnapshot(
        STOCK_LIBRARY_READY,
        STOCK_LIBRARY_RUNTIME_SOURCE,
        tuple(records),
    )
    return lambda _project_root=None: snapshot


class IndicatorFollowSignalValidationRecentStocksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def _selector(self):
        selector = IndicatorFollowSignalValidationStockDisplay()
        selector.resize(320, 30)
        selector.show()
        self.widgets.append(selector)
        self.app.processEvents()
        return selector

    def test_mru_order_dedup_limit_and_restart_restore(self):
        records = [_record(index) for index in range(1, 18)]
        with tempfile.TemporaryDirectory() as temp_dir:
            ini_path = str(Path(temp_dir) / "v2-recent.ini")
            settings = QSettings(ini_path, QSettings.IniFormat)
            store = IndicatorFollowSignalValidationRecentStockStore(
                settings=settings,
                snapshot_loader=_loader(records),
            )
            self.assertEqual((), store.recent_stocks)

            a = ValidationStockRef("000001", "저장시이름")
            b = ValidationStockRef("000002", "종목2")
            self.assertTrue(store.activate(a))
            self.assertTrue(store.activate(b))
            self.assertTrue(store.activate(a))
            self.assertFalse(store.activate(ValidationStockRef("000001", "종목1")))
            self.assertEqual(("000001", "000002"), tuple(
                stock.code for stock in store.recent_stocks
            ))
            self.assertEqual("종목1", store.recent_stocks[0].name)

            for index in range(3, 18):
                store.activate(ValidationStockRef(f"{index:06d}", f"종목{index}"))
            self.assertEqual(MAX_RECENT_STOCKS, len(store.recent_stocks))
            self.assertEqual("000017", store.recent_stocks[0].code)
            self.assertNotIn("000002", {stock.code for stock in store.recent_stocks})

            restored = IndicatorFollowSignalValidationRecentStockStore(
                settings=QSettings(ini_path, QSettings.IniFormat),
                snapshot_loader=_loader(records),
            )
            self.assertEqual(store.recent_stocks, restored.recent_stocks)
            flow = IndicatorFollowSignalValidationFlow(
                _ConnectedBroker(),
                recent_stock_store=restored,
            )
            self.assertEqual(restored.recent_stocks[0], flow.last_selected_stock)

    def test_bad_preferences_and_unverified_library_fail_empty_without_rewrite(self):
        records = [_record(1), _record(2)]
        for raw_value in ("{bad json", json.dumps({"code": "000001"})):
            settings = _FakeSettings(raw_value)
            store = IndicatorFollowSignalValidationRecentStockStore(
                settings=settings,
                snapshot_loader=_loader(records),
            )
            self.assertEqual((), store.recent_stocks)
            self.assertEqual([], settings.set_calls)
            self.assertEqual(0, settings.sync_calls)

        mixed = json.dumps([
            {"code": "000001", "name": "오래된이름"},
            {"code": "000001", "name": "중복"},
            {"code": "999999", "name": "없음"},
            {"code": "", "name": "누락"},
            "not-a-dict",
        ])
        settings = _FakeSettings(mixed)
        store = IndicatorFollowSignalValidationRecentStockStore(
            settings=settings,
            snapshot_loader=_loader(records),
        )
        self.assertEqual((ValidationStockRef("000001", "종목1"),), store.recent_stocks)
        self.assertEqual([], settings.set_calls)

        unavailable = IndicatorFollowSignalValidationRecentStockStore(
            settings=_FakeSettings(mixed),
            snapshot_loader=lambda _root=None: StockLibraryLoadSnapshot(
                "FAILED", "EMPTY", (), "UNAVAILABLE"
            ),
        )
        self.assertEqual((), unavailable.recent_stocks)

    def test_settings_write_occurs_only_when_mru_order_changes(self):
        settings = _FakeSettings("")
        store = IndicatorFollowSignalValidationRecentStockStore(
            settings=settings,
            snapshot_loader=_loader([_record(1), _record(2)]),
        )
        a = ValidationStockRef("000001", "종목1")
        b = ValidationStockRef("000002", "종목2")
        self.assertTrue(store.activate(a))
        self.assertFalse(store.activate(a))
        self.assertTrue(store.activate(b))
        self.assertTrue(store.activate(a))
        self.assertEqual(3, len(settings.set_calls))
        self.assertEqual(3, settings.sync_calls)
        payload = json.loads(settings.stored_value)
        self.assertEqual(["000001", "000002"], [item["code"] for item in payload])

    def test_arrow_popup_and_plain_stock_label_click_contract(self):
        selector = self._selector()
        selector.set_recent_stocks((
            ValidationStockRef("005930", "삼성전자"),
            ValidationStockRef("000660", "SK하이닉스"),
        ))
        selections = []
        selector.full_stock_selection_requested.connect(lambda: selections.append(True))

        QTest.mouseClick(selector.stock_label, Qt.LeftButton)
        self.assertEqual([], selections)
        self.assertFalse(selector.recent_popup.isVisible())
        QTest.mouseDClick(selector.stock_label, Qt.LeftButton)
        self.assertEqual([True], selections)

        QTest.mouseClick(selector.stock_label, Qt.RightButton)
        self.assertEqual([True], selections)
        self.assertFalse(selector.recent_popup.isVisible())

        QTest.mouseClick(selector.arrow_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(selector.recent_popup.isVisible())
        self.assertTrue(selector.recent_popup.windowFlags() & Qt.Popup)
        QTest.mouseClick(selector.arrow_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(selector.recent_popup.isVisible())

    def test_recent_popup_emits_only_explicit_activated_selection(self):
        selector = self._selector()
        stocks = tuple(
            ValidationStockRef(f"{index:06d}", f"종목{index}")
            for index in range(1, 18)
        )
        selected = []
        selector.recent_stock_activated.connect(selected.append)
        selector.set_recent_stocks(stocks)
        selector.set_current_stock(stocks[0])
        self.assertEqual([], selected)
        self.assertEqual(15, len(selector.recent_stocks))
        self.assertEqual(15, selector.recent_popup.stock_list.count())
        self.assertFalse(isinstance(selector.stock_label, QComboBox))
        self.assertEqual("000001 종목1", selector.stock_label.text())

        QTest.mouseClick(selector.arrow_button, Qt.LeftButton)
        self.app.processEvents()
        item = selector.recent_popup.stock_list.item(1)
        selector.recent_popup.stock_list.itemClicked.emit(item)
        self.assertEqual([stocks[1]], selected)
        self.assertFalse(selector.recent_popup.isVisible())

    def test_tooltip_uses_only_loaded_static_metadata_and_hides_on_boundaries(self):
        selector = self._selector()
        stock = ValidationStockRef("005930", "삼성전자")
        metadata = {
            "market": "KOSPI",
            "classification": "일반종목",
            "nxt_available": True,
            "status": "정상 | 증거금40% | 신용가능 | 담보대출",
            "master_stock_state": "거래정상",
            "master_construction": "정상",
            "master_stock_info": "시장구분0|코스피",
            "master_stock_market_kind": "대형주",
            "current_price": 249500,
            "open_price": 249500,
            "high_price": 250500,
            "low_price": 249000,
            "change_rate": -3.85,
            "previous_day_volume_rate": -38.26,
            "execution_strength": 83.9,
        }
        with patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.showText"
        ) as show_tooltip, patch(
            "gui_indicator_follow_signal_validation_window.QToolTip.hideText"
        ) as hide_tooltip:
            selector.set_current_stock(stock, metadata)
            tooltip = selector.tooltip_text
            self.assertEqual(
                "▪  005930 삼성전자  |  KOSPI  |  상태 정상  |  NXT\n"
                "▪  현재가 249,500  |  시가 249,500  |  고가 250,500  |  저가 249,000\n"
                "▪  등락률 -3.85%  |  전일대비 -38.26%  |  체결강도 83.9",
                tooltip,
            )
            self.assertEqual(3, len(tooltip.splitlines()))
            for excluded in (
                "분류", "기초상태", "거래정상", "시장구분", "대형주",
                "증거금", "신용가능", "담보대출",
            ):
                self.assertNotIn(excluded, tooltip)
            QApplication.sendEvent(selector.stock_label, QEvent(QEvent.Enter))
            show_tooltip.assert_called_once()
            QApplication.sendEvent(selector.stock_label, QEvent(QEvent.Leave))
            self.assertTrue(hide_tooltip.called)

            hide_tooltip.reset_mock()
            selector.set_current_stock(ValidationStockRef("000660", "SK하이닉스"), {})
            hide_tooltip.assert_called_once()
            self.assertEqual(
                "▪  000660 SK하이닉스  |  -  |  상태 -\n"
                "▪  현재가 -  |  시가 -  |  고가 -  |  저가 -\n"
                "▪  등락률 -  |  전일대비 -  |  체결강도 -",
                selector.tooltip_text,
            )

            selector.set_current_stock(
                ValidationStockRef("035420", "NAVER"),
                {"status": "투자경고 | 관리종목 | 증거금100% | 신용가능"},
            )
            self.assertIn("상태 투자경고 | 관리종목", selector.tooltip_text)
            self.assertNotIn("증거금", selector.tooltip_text)
            self.assertNotIn("신용가능", selector.tooltip_text)

            show_tooltip.reset_mock()
            selector.set_current_stock(None)
            QApplication.sendEvent(selector.stock_label, QEvent(QEvent.Enter))
            show_tooltip.assert_not_called()
            self.assertEqual("", selector.tooltip_text)

    def test_source_boundaries_exclude_main_runtime_broker_and_mock_resolvers(self):
        sources = "\n".join(
            (self.project_root / path).read_text(encoding="utf-8")
            for path in (
                "gui_indicator_follow_signal_validation_window.py",
                "indicator_follow_signal_validation_recent_stocks.py",
            )
        )
        for forbidden in (
            "gui_main_table_loader",
            "main_monitoring_auto_trade_operation_host",
            "_main_stock_live_tooltip",
            "request_initial_market_snapshot",
            "request_stock_ranking_snapshot",
            "SetRealReg",
            "gui_market_data_host",
            "mock_validation",
        ):
            self.assertNotIn(forbidden, sources)


if __name__ == "__main__":
    unittest.main()
