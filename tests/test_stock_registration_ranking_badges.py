# -*- coding: utf-8 -*-

from __future__ import annotations

from collections import deque
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog

import gui_auto_trade_setting_window as setting_window
from kiwoom_api import KiwoomApi
from kiwoom_realtime_shadow import RealtimeShadowBarBuilder
from kiwoom_screen_allocator import KiwoomScreenAllocator


class _Signal:
    def __init__(self) -> None:
        self.values = []

    def emit(self, value) -> None:
        self.values.append(value)


class _Control:
    def __init__(self) -> None:
        self.calls = []
        self.tr_rows = []

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        if signature.startswith("GetConnectState"):
            return 1
        if signature.startswith("GetRepeatCnt"):
            return len(self.tr_rows)
        if signature.startswith("GetCommData"):
            return self.tr_rows[int(args[2])].get(str(args[3]), "")
        return 0


def _api() -> KiwoomApi:
    api = KiwoomApi.__new__(KiwoomApi)
    api._control = _Control()
    api._available = True
    api._connected = True
    api._login_requested = False
    api._login_session_id = "SESSION-RANKING"
    api._connection_epoch = 7
    api.last_login_error = 0
    api.last_login_message = "connected"
    api._unavailable_reason = ""
    api._screen_allocator = KiwoomScreenAllocator()
    api._realtime_shadow_builder = RealtimeShadowBarBuilder()
    api._realtime_shadow_registration = api._empty_realtime_shadow_snapshot()
    api._realtime_receive_sequence = 0
    api._pending_tr = {}
    api._tr_request_queue = deque()
    api._tr_last_dispatch_monotonic_ms = None
    api._tr_governor_timer_scheduled = False
    api._tr_governor_dispatching = False
    api._tr_governor_total_enqueued = 0
    api._tr_governor_total_dispatched = 0
    api._tr_governor_last_rqname = ""
    api._tr_governor_last_trcode = ""
    api._tr_governor_dispatch_history = deque(maxlen=4096)
    api._tr_governor_last_queue_wait_ms = 0.0
    api._tr_governor_max_queue_wait_ms = 0.0
    api._tr_governor_timeout_count = 0
    api._tr_governor_stale_count = 0
    api._tr_governor_error_count = 0
    api._tr_governor_last_error_reason = ""
    api.realtime_shadow_tick_received = _Signal()
    api.realtime_shadow_bar_completed = _Signal()
    api.login_state_changed = _Signal()
    return api


class StockRankingBrokerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_each_source_uses_one_governed_ranking_request_with_integrated_market(self) -> None:
        expected = {
            "VOLUME_TOP": ("OPT10030", "1"),
            "VALUE_TOP": ("OPT10032", None),
            "RISE_TOP": ("OPT10027", "1"),
            "FALL_TOP": ("OPT10027", "3"),
        }
        for source, (trcode, sort_value) in expected.items():
            with self.subTest(source=source):
                api = _api()
                callbacks = []
                with patch("kiwoom_api.QTimer.singleShot"):
                    result = api.request_stock_ranking_snapshot(
                        source,
                        callback=callbacks.append,
                    )

                comm_calls = [
                    call
                    for call in api._control.calls
                    if call[0].startswith("CommRqData")
                ]
                set_inputs = [
                    call[1]
                    for call in api._control.calls
                    if call[0].startswith("SetInputValue")
                ]
                self.assertEqual(1, len(comm_calls))
                self.assertEqual(trcode, comm_calls[0][1][1])
                self.assertIn(("시장구분", "000"), set_inputs)
                self.assertIn(("거래소구분", "1"), set_inputs)
                if sort_value is not None:
                    self.assertIn(("정렬구분", sort_value), set_inputs)
                self.assertEqual((1, 0), (
                    result["ranking_tr_count"],
                    result["continuation_tr_count"],
                ))
                self.assertEqual(1, api.tr_governor_metrics_snapshot().total_enqueued)
                self.assertFalse(any(
                    call[0].startswith(("CommKwRqData", "SetRealReg", "SendOrder"))
                    for call in api._control.calls
                ))

    def test_response_reuses_generic_rows_limits_top100_and_preserves_session_identity(self) -> None:
        api = _api()
        api._control.tr_rows = [
            {
                "종목코드": f"A{index:06d}",
                "종목명": f"종목{index}",
                "현재가": f"-{1000 + index}",
                "등락률": "+1.25",
                "현재거래량": str(10000 + index),
                "거래대금": str(100000 + index),
            }
            for index in range(1, 121)
        ]
        callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_stock_ranking_snapshot(
                "VALUE_TOP",
                callback=callbacks.append,
            )
        api._on_receive_tr_data(
            "4000",
            result["rqname"],
            "OPT10032",
            "",
            "2",
        )

        self.assertEqual(1, len(callbacks))
        payload = callbacks[0]
        self.assertEqual(100, payload["rows_count"])
        self.assertEqual("000001", payload["rows"][0]["stock_code"])
        self.assertEqual(1001, payload["rows"][0]["current_price"])
        self.assertEqual(7, payload["connection_epoch"])
        self.assertEqual("SESSION-RANKING", payload["login_session_id"])
        self.assertEqual(0, payload["continuation_tr_count"])
        self.assertNotIn(result["rqname"], api._pending_tr)

    def test_old_broker_session_ranking_response_is_fail_closed(self) -> None:
        api = _api()
        callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_stock_ranking_snapshot(
                "VOLUME_TOP",
                callback=callbacks.append,
            )
        api._connection_epoch = 8
        api._login_session_id = "SESSION-NEW"
        api._on_receive_tr_data(
            "4000",
            result["rqname"],
            "OPT10030",
            "",
            "0",
        )

        self.assertEqual("STALE_BROKER_SESSION", callbacks[0]["error_kind"])
        self.assertEqual(1, api.tr_governor_metrics_snapshot().stale_count)
        self.assertNotIn(result["rqname"], api._pending_tr)

    def test_unsupported_source_and_timeout_are_fail_closed_without_extra_request(self) -> None:
        api = _api()
        unsupported_callbacks = []
        unsupported = api.request_stock_ranking_snapshot(
            "UNKNOWN",
            callback=unsupported_callbacks.append,
        )
        self.assertFalse(unsupported["ok"])
        self.assertEqual(
            "UNSUPPORTED_STOCK_RANKING_SOURCE",
            unsupported_callbacks[0]["error_kind"],
        )
        self.assertFalse(any(
            call[0].startswith("CommRqData") for call in api._control.calls
        ))

        timeout_callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_stock_ranking_snapshot(
                "FALL_TOP",
                callback=timeout_callbacks.append,
            )
        api._expire_stock_ranking_snapshot(result["rqname"])
        self.assertEqual("TIMEOUT", timeout_callbacks[0]["error_kind"])
        self.assertEqual(1, api.tr_governor_metrics_snapshot().timeout_count)
        self.assertNotIn(result["rqname"], api._pending_tr)


class _DialogApi:
    def __init__(self) -> None:
        self.ranking_requests = []
        self.snapshot_requests = []

    def request_stock_ranking_snapshot(self, source, *, callback=None):
        self.ranking_requests.append((str(source), callback))
        return {
            "ok": True,
            "status": "ENQUEUED",
            "source": str(source),
            "ranking_tr_count": 1,
        }

    def request_initial_market_snapshot(self, stock_codes, *, callback=None):
        codes = tuple(stock_codes)
        self.snapshot_requests.append((codes, callback))
        return {
            "ok": True,
            "status": "ENQUEUED",
            "target_stock_codes": list(codes),
            "batch_count": 1,
        }

    def emit_ranking(self, index, payload) -> None:
        self.ranking_requests[index][1](payload)

    def emit_snapshot(self, index, payload) -> None:
        self.snapshot_requests[index][1](payload)


class StockRegistrationRankingBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, api: _DialogApi):
        library = (
            {
                "code": "111111",
                "name": "첫종목",
                "market": "KOSPI",
                "nxt_available": True,
                "status": "정상",
            },
            {
                "code": "222222",
                "name": "둘종목",
                "market": "KOSDAQ",
                "nxt_available": False,
                "status": "투자경고 | 관리종목",
            },
        )
        snapshot = SimpleNamespace(source="TEST_LIBRARY", records=library)
        library_patch = patch.object(
            setting_window,
            "load_stock_library_snapshot",
            return_value=snapshot,
        )
        stocks_patch = patch.object(setting_window, "read_base_stocks", return_value=[])
        library_patch.start()
        stocks_patch.start()
        self.addCleanup(library_patch.stop)
        self.addCleanup(stocks_patch.stop)
        dialog = setting_window.InstanceStockSearchRegisterDialog(
            None,
            instance_metadata={"target_kind": "unassigned"},
            kiwoom_api=api,
        )
        self.addCleanup(dialog.close)
        return dialog

    @staticmethod
    def _codes(dialog) -> list[str]:
        return [
            dialog.result_table.item(row, dialog.CODE_COLUMN).text()
            for row in range(dialog.result_table.rowCount())
        ]

    def test_four_equal_width_badges_share_the_existing_dialog_and_table(self) -> None:
        dialog = self._dialog(_DialogApi())
        dialog.show()
        self.app.processEvents()
        self.assertEqual(
            ["거래량", "거래대금", "급상승", "급하락"],
            [button.text() for button in dialog.ranking_buttons.values()],
        )
        self.assertEqual("일반종목", dialog.general_stock_button.text())
        self.assertEqual("|", dialog.ranking_separator_label.text())
        self.assertEqual("TOP100 :", dialog.ranking_title_label.text())
        expected_width = (
            dialog.ranking_buttons["VALUE_TOP"].fontMetrics().horizontalAdvance(
                "거래대금"
            )
            + (dialog.RANKING_BADGE_HORIZONTAL_PADDING * 2)
            + 2
        )
        self.assertEqual(
            {expected_width},
            {button.width() for button in dialog.ranking_buttons.values()},
        )
        badge_geometries = [
            button.geometry() for button in dialog.ranking_buttons.values()
        ]
        self.assertTrue(all(
            right.left() - left.right() - 1 >= 6
            for left, right in zip(badge_geometries, badge_geometries[1:])
        ))
        self.assertTrue(all(button.parent() is dialog for button in dialog.ranking_buttons.values()))
        self.assertEqual([], dialog.findChildren(QDialog))
        self.assertLessEqual(
            abs(
                dialog.ranking_buttons["FALL_TOP"].geometry().right()
                - dialog.result_table.geometry().right()
            ),
            2,
        )
        volume_badge_geometry = dialog.ranking_buttons["VOLUME_TOP"].geometry()
        search_geometry = dialog.search_input.geometry()
        self.assertLessEqual(
            abs(volume_badge_geometry.bottom() - search_geometry.bottom()),
            1,
        )
        self.assertGreater(
            volume_badge_geometry.center().y(),
            search_geometry.center().y(),
        )
        badge_table_gap = (
            dialog.result_table.geometry().top() - volume_badge_geometry.bottom()
        )
        self.assertGreaterEqual(badge_table_gap, 6)
        self.assertLessEqual(
            badge_table_gap,
            7,
        )
        self.assertIn("font-weight: 700", dialog.ranking_title_label.styleSheet())
        inactive_color = dialog.REGISTRATION_BADGE_INACTIVE_COLOR
        self.assertEqual("#4B5563", inactive_color)
        self.assertTrue(all(
            f"color: {inactive_color}" in button.styleSheet()
            and f"border: 1px solid {inactive_color}" in button.styleSheet()
            and (
                f"padding-left: {dialog.RANKING_BADGE_HORIZONTAL_PADDING}px"
                in button.styleSheet()
            )
            for button in dialog.ranking_buttons.values()
        ))

    def test_ranking_uses_same_table_selection_status_and_one_snapshot_batch(self) -> None:
        api = _DialogApi()
        dialog = self._dialog(api)
        dialog.request_stock_ranking("VOLUME_TOP")
        api.emit_ranking(
            0,
            {
                "ok": True,
                "source": "VOLUME_TOP",
                "rows": [
                    {
                        "stock_code": "222222",
                        "stock_name": "둘종목",
                        "current_price": 2000,
                        "cumulative_volume": 50000,
                    },
                    {
                        "stock_code": "111111",
                        "stock_name": "첫종목",
                        "current_price": 1000,
                        "cumulative_volume": 40000,
                    },
                ],
            },
        )

        self.assertEqual("VOLUME_TOP", dialog._result_source)
        self.assertEqual(["222222", "111111"], self._codes(dialog))
        self.assertEqual(1, len(api.snapshot_requests))
        self.assertEqual(("111111", "222222"), api.snapshot_requests[0][0])
        status = dialog.result_table.item(0, dialog.STOCK_STATUS_COLUMN)
        self.assertEqual("투자경고 | 관리종목", status.toolTip())
        dialog.result_table.selectRow(0)
        self.assertEqual([("222222", "둘종목")], dialog._selected_result_stocks())
        self.assertEqual(
            "VOLUME_TOP",
            dialog.result_table.item(0, 0).data(dialog.RESULT_EVIDENCE_ROLE),
        )
        active_style = dialog.ranking_buttons["VOLUME_TOP"].styleSheet()
        self.assertIn(
            f"color: {setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR}",
            active_style,
        )
        self.assertIn(
            f"border: 1px solid {setting_window.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR}",
            active_style,
        )
        self.assertIn(
            f"color: {dialog.REGISTRATION_BADGE_INACTIVE_COLOR}",
            dialog.ranking_buttons["VALUE_TOP"].styleSheet(),
        )
        highlight_color = setting_window.AUTO_TRADE_SETTING_AMBER_TEXT_COLOR.lower()
        volume_header = dialog.result_table.horizontalHeaderItem(dialog.VOLUME_COLUMN)
        volume_item = dialog.result_table.item(0, dialog.VOLUME_COLUMN)
        self.assertEqual(highlight_color, volume_header.foreground().color().name())
        self.assertEqual(highlight_color, volume_item.foreground().color().name())
        highlight_background = dialog.RANKING_HIGHLIGHT_BACKGROUND_COLOR.lower()
        self.assertEqual(
            "#ffffff",
            volume_header.background().color().name(),
        )
        self.assertEqual(
            highlight_background,
            volume_item.background().color().name(),
        )
        self.assertEqual(
            Qt.NoBrush,
            dialog.result_table.horizontalHeaderItem(
                dialog.TRADING_VALUE_COLUMN
            ).foreground().style(),
        )

        dialog.search_input.setText("첫종목")
        dialog.search_stocks()
        self.assertTrue(all(
            f"color: {dialog.REGISTRATION_BADGE_INACTIVE_COLOR}"
            in button.styleSheet()
            for button in dialog.ranking_buttons.values()
        ))
        self.assertEqual(Qt.NoBrush, volume_header.foreground().style())
        self.assertEqual("#ffffff", volume_header.background().color().name())
        search_row = dialog._find_result_row_by_stock_code("111111")
        self.assertEqual(
            Qt.NoBrush,
            dialog.result_table.item(
                search_row,
                dialog.VOLUME_COLUMN,
            ).background().style(),
        )

    def test_source_switch_drops_old_response_and_sort_reclick_do_not_auto_refresh(self) -> None:
        api = _DialogApi()
        dialog = self._dialog(api)
        dialog.request_stock_ranking("VOLUME_TOP")
        dialog.request_stock_ranking("RISE_TOP")
        api.emit_ranking(
            0,
            {
                "ok": True,
                "source": "VOLUME_TOP",
                "rows": [{"stock_code": "111111", "stock_name": "첫종목"}],
            },
        )
        self.assertEqual(0, dialog.result_table.rowCount())
        self.assertEqual(0, len(api.snapshot_requests))

        api.emit_ranking(
            1,
            {
                "ok": True,
                "source": "RISE_TOP",
                "rows": [{"stock_code": "222222", "stock_name": "둘종목"}],
            },
        )
        self.assertEqual(["222222"], self._codes(dialog))
        self.assertEqual(1, len(api.snapshot_requests))

        dialog.on_result_header_clicked(dialog.CURRENT_PRICE_COLUMN)
        dialog.result_table.verticalScrollBar().setValue(1)
        self.app.processEvents()
        self.assertEqual((2, 1), (
            len(api.ranking_requests),
            len(api.snapshot_requests),
        ))

        dialog.request_stock_ranking("RISE_TOP")
        self.assertEqual(3, len(api.ranking_requests))
        self.assertEqual(1, len(api.snapshot_requests))

    def test_each_badge_cost_is_one_ranking_plus_one_optkwfid_snapshot(self) -> None:
        expected_columns = {
            "VOLUME_TOP": setting_window.InstanceStockSearchRegisterDialog.VOLUME_COLUMN,
            "VALUE_TOP": setting_window.InstanceStockSearchRegisterDialog.TRADING_VALUE_COLUMN,
            "RISE_TOP": setting_window.InstanceStockSearchRegisterDialog.CHANGE_RATE_COLUMN,
            "FALL_TOP": setting_window.InstanceStockSearchRegisterDialog.CHANGE_RATE_COLUMN,
        }
        for source, _text in setting_window.InstanceStockSearchRegisterDialog.RANKING_BADGES:
            with self.subTest(source=source):
                api = _DialogApi()
                dialog = self._dialog(api)
                dialog.request_stock_ranking(source)
                api.emit_ranking(
                    0,
                    {
                        "ok": True,
                        "source": source,
                        "rows": [
                            {"stock_code": "111111", "stock_name": "첫종목"}
                        ],
                    },
                )
                self.assertEqual((1, 1), (
                    len(api.ranking_requests),
                    len(api.snapshot_requests),
                ))
                target_column = expected_columns[source]
                expected_color = (
                    setting_window.AUTO_TRADE_SETTING_AMBER_TEXT_COLOR.lower()
                )
                self.assertEqual(
                    expected_color,
                    dialog.result_table.horizontalHeaderItem(
                        target_column
                    ).foreground().color().name(),
                )
                self.assertEqual(
                    expected_color,
                    dialog.result_table.item(
                        0,
                        target_column,
                    ).foreground().color().name(),
                )
                expected_background = dialog.RANKING_HIGHLIGHT_BACKGROUND_COLOR.lower()
                self.assertEqual(
                    "#ffffff",
                    dialog.result_table.horizontalHeaderItem(
                        target_column
                    ).background().color().name(),
                )
                self.assertEqual(
                    expected_background,
                    dialog.result_table.item(
                        0,
                        target_column,
                    ).background().color().name(),
                )


if __name__ == "__main__":
    unittest.main()
