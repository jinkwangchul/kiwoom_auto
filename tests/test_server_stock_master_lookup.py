from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget

import gui_auto_trade_setting_window as setting_window
import gui_stock_register_window as stock_register_window
from kiwoom_api import KiwoomApi
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
)


class _MasterControl:
    def __init__(self, values) -> None:
        self.values = list(values)
        self.calls = []

    def dynamicCall(self, signature, *arguments):
        argument = arguments[0] if len(arguments) == 1 else tuple(arguments)
        self.calls.append((signature, argument))
        return self.values.pop(0)


class ServerStockMasterLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    @staticmethod
    def _api(control, *, ready=True):
        api = KiwoomApi.__new__(KiwoomApi)
        api._control = control
        api.broker_readiness_snapshot = lambda: SimpleNamespace(
            broker_request_ready=ready,
            reason="READY" if ready else "DISCONNECTED",
        )
        return api

    def test_market_code_normalization_removes_empty_and_duplicates(self) -> None:
        self.assertEqual(
            ["005930", "000660", "00088K"],
            KiwoomApi._normalize_master_code_list(" 005930;000660;005930;00088k; "),
        )

    def test_etn_market_kind_uses_official_master_function_without_tr(self) -> None:
        control = _MasterControl(["60"])
        api = self._api(control)

        result = api.get_master_stock_market_kind("520046")

        self.assertTrue(result["ok"])
        self.assertEqual("60", result["value"])
        self.assertEqual(
            [
                (
                    "KOA_Functions(QString, QString)",
                    ("GetStockMarketKind", "520046"),
                )
            ],
            control.calls,
        )

    def test_market_and_name_wrappers_use_only_master_calls(self) -> None:
        control = _MasterControl(
            [
                "005930;000660;005930;",
                " 삼성전자 ",
                "정상|거래정지",
                "투자경고",
                "시장구분0|코스피;시장구분1|중형주;업종구분|금융업;",
            ]
        )
        api = self._api(control)
        self.assertEqual(["005930", "000660"], api.get_market_stock_codes("0")["value"])
        self.assertEqual("삼성전자", api.get_master_stock_name("005930")["value"])
        self.assertEqual("정상|거래정지", api.get_master_stock_state("0134x0")["value"])
        self.assertEqual("투자경고", api.get_master_construction("0164h0")["value"])
        self.assertIn("시장구분1|중형주", api.get_master_stock_info("005930")["value"])
        self.assertEqual(
            [
                "GetCodeListByMarket(QString)",
                "GetMasterCodeName(QString)",
                "GetMasterStockState(QString)",
                "GetMasterConstruction(QString)",
                "KOA_Functions(QString, QString)",
            ],
            [signature for signature, _argument in control.calls],
        )
        self.assertEqual(
            [
                "0",
                "005930",
                "0134X0",
                "0164H0",
                ("GetMasterStockInfo", "005930"),
            ],
            [arg for _sig, arg in control.calls],
        )

    def test_empty_name_is_distinct_failure(self) -> None:
        api = self._api(_MasterControl(["  "]))
        result = api.get_master_stock_name("005930")
        self.assertFalse(result["ok"])
        self.assertEqual("MASTER_NAME_EMPTY", result["reason"])

    def test_disconnected_master_wrapper_is_fail_closed(self) -> None:
        control = _MasterControl([])
        api = self._api(control, ready=False)
        self.assertFalse(api.get_market_stock_codes("0")["ok"])
        self.assertFalse(api.get_master_stock_name("005930")["ok"])
        self.assertFalse(api.get_master_stock_state("005930")["ok"])
        self.assertFalse(api.get_master_construction("005930")["ok"])
        self.assertFalse(api.get_master_stock_info("005930")["ok"])
        self.assertFalse(api.get_master_stock_market_kind("005930")["ok"])
        self.assertEqual([], control.calls)

    def test_master_stock_info_decodes_legacy_koa_cp949_transport(self) -> None:
        expected = "시장구분0|코스닥|중견기업;시장구분1|소형주;"
        transported = expected.encode("cp949").decode("latin-1")
        api = self._api(_MasterControl([transported]))

        result = api.get_master_stock_info("0134x0")

        self.assertTrue(result["ok"])
        self.assertEqual(expected, result["value"])

    def test_dialog_uses_local_library_even_when_legacy_server_args_are_passed(self) -> None:
        api = MagicMock()
        library = [{"code": "123456", "name": "캐시종목", "market": "KOSDAQ", "chosung": "ㅋㅅㅈㅁ"}]
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            tuple(library),
        )
        with (
            patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot),
            patch.object(setting_window, "find_library_stock_by_code", return_value=library[0]),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                None,
                stock_source="server",
                kiwoom_api=api,
            )
            self.addCleanup(dialog.close)
            dialog.search_input.setText("캐시")
            dialog.search_stocks()
            self.assertEqual(STOCK_LIBRARY_RUNTIME_SOURCE, dialog.stock_source)
            self.assertEqual(1, dialog.result_table.rowCount())
            self.assertTrue(dialog._valid_result_stock(0, "123456", "캐시종목"))
        api.assert_not_called()

    def test_local_verified_registration_reuses_existing_writer(self) -> None:
        library = [{"code": "123456", "name": "캐시종목", "market": "KOSDAQ", "chosung": "ㅋㅅㅈㅁ"}]
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            tuple(library),
        )
        with (
            patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot),
            patch.object(setting_window, "find_library_stock_by_code", return_value=library[0]),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                None,
                instance_metadata={
                    "target_kind": "unassigned",
                    "instance_name": "등록대기",
                },
            )
            self.addCleanup(dialog.close)
            dialog.search_input.setText("캐시")
            dialog.search_stocks()
            dialog.result_table.selectRow(0)
            with (
                patch.object(setting_window, "read_base_stocks", return_value=[]),
                patch.object(setting_window, "append_base_stock", return_value=True) as writer,
                patch.object(dialog, "_refresh_parent_views"),
                patch.object(dialog, "_refresh_classification_for_stock"),
                patch.object(setting_window, "show_toast") as toast,
            ):
                self.assertTrue(dialog.register_selected_result_rows())
        writer.assert_called_once_with("123456", "캐시종목")
        toast.assert_called_once_with(dialog, "등록 1건")

    def test_search_register_control_opens_local_library_dialog(self) -> None:
        created = []

        class FakeDialog:
            def __init__(self, parent, **kwargs):
                self.parent = parent
                self.kwargs = kwargs
                self.finished = MagicMock()
                self.destroyed = MagicMock()
                self.setAttribute = MagicMock()
                self.show = MagicMock()
                self.raise_ = MagicMock()
                self.activateWindow = MagicMock()
                created.append(self)

        host = QWidget()
        host.refresh_stock_table = MagicMock()
        host._stock_search_register_opener = setting_window.open_instance_stock_search_register_dialog
        self.addCleanup(host.close)
        with patch.object(setting_window, "InstanceStockSearchRegisterDialog", FakeDialog):
            stock_register_window.StockRegisterWindow.open_search_register_dialog(host)

        self.assertNotIn("stock_source", created[0].kwargs)
        self.assertNotIn("kiwoom_api", created[0].kwargs)
        created[0].show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
