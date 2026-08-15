from __future__ import annotations

from datetime import datetime
import importlib
import sys
import types
import unittest


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.values: list[object] = []

    def connect(self, _callback) -> None:
        self.callbacks.append(_callback)

    def emit(self, _value) -> None:
        self.values.append(_value)
        for callback in tuple(self.callbacks):
            callback(_value)


class _Timer:
    callbacks: list[object] = []

    @classmethod
    def singleShot(cls, _timeout_ms: int, callback) -> None:
        cls.callbacks.append(callback)


class _Application:
    @staticmethod
    def instance():
        return object()


class _Control:
    def __init__(self, account_no: str) -> None:
        self.account_no = account_no
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[dict[str, object]] = []
        self.summary: dict[str, object] = {}
        self.server_gubun = "0"
        self.comm_rq_result = 0

    def dynamicCall(self, signature: str, *args):
        self.calls.append((signature, args))
        if signature == "GetConnectState()":
            return 1
        if signature == "GetLoginInfo(QString)":
            if args and args[0] == "GetServerGubun":
                return self.server_gubun
            return f"{self.account_no};"
        if signature.startswith("SetInputValue"):
            return None
        if signature.startswith("CommRqData"):
            return self.comm_rq_result
        if signature.startswith("KOA_Functions"):
            return ""
        if signature.startswith("GetRepeatCnt"):
            return len(self.rows)
        if signature.startswith("GetCommData"):
            _trcode, _rqname, index, field = args
            if str(field) in self.summary:
                return self.summary[str(field)]
            return self.rows[int(index)].get(str(field), "")
        raise AssertionError(f"unexpected dynamicCall: {signature}")


def _load_kiwoom_api_module():
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QObject = object
    qtcore.QTimer = _Timer
    qtcore.pyqtSignal = lambda *_args, **_kwargs: _Signal()
    qtwidgets = types.ModuleType("PyQt5.QtWidgets")
    qtwidgets.QApplication = _Application
    qax = types.ModuleType("PyQt5.QAxContainer")
    qax.QAxWidget = object
    pyqt = types.ModuleType("PyQt5")
    sys.modules["PyQt5"] = pyqt
    sys.modules["PyQt5.QtCore"] = qtcore
    sys.modules["PyQt5.QtWidgets"] = qtwidgets
    sys.modules["PyQt5.QAxContainer"] = qax
    sys.modules.pop("kiwoom_api", None)
    return importlib.import_module("kiwoom_api")


class KiwoomRecoverySnapshotAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._saved_modules = {
            name: sys.modules.get(name)
            for name in (
                "PyQt5",
                "PyQt5.QtCore",
                "PyQt5.QtWidgets",
                "PyQt5.QAxContainer",
                "kiwoom_api",
            )
        }
        cls.module = _load_kiwoom_api_module()

    @classmethod
    def tearDownClass(cls) -> None:
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self) -> None:
        _Timer.callbacks.clear()
        self.account_no = "1234567890"
        self.control = _Control(self.account_no)
        self.api = self.module.KiwoomApi.__new__(self.module.KiwoomApi)
        self.api._control = self.control
        self.api._available = True
        self.api._connected = True
        self.api._login_session_id = "KIWOOM_LOGIN_SESSION_TEST"
        self.api._unavailable_reason = ""
        self.api._pending_tr = {}
        self.api._account_funds_request_accounts = {}
        self.identity = self.module.RecoverySessionIdentity(
            recovery_session_id="RECOVERY_SESSION_TEST",
            login_session_id="KIWOOM_LOGIN_SESSION_TEST",
            account_no=self.account_no,
            trading_day="2026-07-27",
            requested_at="2026-07-27T09:00:00",
        )

    def holding_row(self, code: str) -> dict[str, object]:
        return {
            "종목번호": f"A{code}",
            "종목명": code,
            "평가손익": "0",
            "수익률(%)": "0",
            "매입가": "1000",
            "보유수량": "2",
            "매매가능수량": "2",
            "현재가": "1000",
            "평가금액": "2000",
        }

    def order_row(self) -> dict[str, object]:
        return {
            "계좌번호": self.account_no,
            "주문번호": "10001",
            "종목코드": "005930",
            "주문상태": "접수",
            "종목명": "삼성전자",
            "주문수량": "10",
            "주문가격": "70000",
            "미체결수량": "3",
            "원주문번호": "",
            "주문구분": "-매도",
            "매매구분": "1",
            "시간": "091500",
            "체결량": "7",
        }

    def test_holdings_adapter_collects_all_continuation_pages(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_account_holdings_snapshot(
            self.identity,
            callback=results.append,
        )
        rqname = str(requested["rqname"])
        self.control.rows = [self.holding_row("005930")]
        self.api._on_receive_tr_data("9101", rqname, "opw00018", "", "2")
        self.assertIn(rqname, self.api._pending_tr)
        self.control.rows = [self.holding_row("006400")]
        self.api._on_receive_tr_data("9101", rqname, "opw00018", "", "0")
        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["is_complete"])
        self.assertEqual(2, results[0]["rows_count"])
        self.assertEqual(2, results[0]["pages"])

    def test_open_orders_adapter_returns_normalized_snapshot(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_open_orders_snapshot(
            self.identity,
            callback=results.append,
        )
        self.control.rows = [self.order_row()]
        self.api._on_receive_tr_data(
            "9102",
            requested["rqname"],
            "opt10075",
            "",
            "0",
        )
        snapshot = results[0]["snapshot"]
        self.assertTrue(snapshot.is_complete)
        self.assertEqual("SELL", snapshot.items[0].order_side)
        self.assertEqual(7, snapshot.items[0].filled_quantity)

    def test_timeout_returns_incomplete_without_writing(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_account_holdings_snapshot(
            self.identity,
            callback=results.append,
        )
        self.api._expire_recovery_snapshot_request(str(requested["rqname"]))
        self.assertEqual(1, len(results))
        self.assertFalse(results[0]["is_complete"])
        self.assertTrue(any("timed out" in error for error in results[0]["errors"]))

    def test_login_account_mismatch_is_rejected_before_tr(self) -> None:
        other_identity = self.module.RecoverySessionIdentity(
            recovery_session_id="RECOVERY_SESSION_OTHER",
            login_session_id="KIWOOM_LOGIN_SESSION_TEST",
            account_no="9999999999",
            trading_day="2026-07-27",
            requested_at=datetime.now().isoformat(),
        )
        result = self.api.request_open_orders_snapshot(other_identity)
        self.assertFalse(result["ok"])
        tr_calls = [
            call for call in self.control.calls if call[0].startswith("CommRqData")
        ]
        self.assertEqual([], tr_calls)

    def test_stale_login_session_is_rejected_before_tr(self) -> None:
        stale_identity = self.module.RecoverySessionIdentity(
            recovery_session_id="RECOVERY_SESSION_STALE",
            login_session_id="KIWOOM_LOGIN_SESSION_OLD",
            account_no=self.account_no,
            trading_day="2026-07-27",
            requested_at=datetime.now().isoformat(),
        )
        result = self.api.request_account_holdings_snapshot(stale_identity)
        self.assertFalse(result["ok"])
        self.assertIn("stale", result["error"])
        tr_calls = [
            call for call in self.control.calls if call[0].startswith("CommRqData")
        ]
        self.assertEqual([], tr_calls)

    def test_existing_minute_candle_response_path_is_unchanged(self) -> None:
        results: list[dict[str, object]] = []
        saved_function = self.module.save_minute_candles_for_stock
        self.module.save_minute_candles_for_stock = (
            lambda _code, _name, rows, max_count: rows[:max_count]
        )
        try:
            self.api._pending_tr["OPT10080_TEST"] = {
                "type": "minute_candles",
                "code": "005930",
                "name": "삼성전자",
                "count": 1,
                "callback": results.append,
            }
            self.control.rows = [
                {
                    "체결시간": "20260727090000",
                    "시가": "70000",
                    "고가": "70100",
                    "저가": "69900",
                    "현재가": "70050",
                    "거래량": "100",
                }
            ]
            self.api._on_receive_tr_data(
                "9001",
                "OPT10080_TEST",
                "opt10080",
                "",
                "0",
            )
        finally:
            self.module.save_minute_candles_for_stock = saved_function
        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["ok"])
        self.assertEqual(1, results[0]["saved_count"])

    def test_account_funds_request_uses_official_opw00001_contract(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_account_funds_snapshot(
            self.account_no,
            request_id=7,
            callback=results.append,
        )

        self.assertTrue(requested["ok"])
        self.assertTrue(str(requested["rqname"]).startswith("OPW00001_ACCOUNT_FUNDS_7_"))
        set_inputs = [args for signature, args in self.control.calls if signature.startswith("SetInputValue")]
        self.assertEqual(
            [
                ("계좌번호", self.account_no),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "2"),
            ],
            set_inputs,
        )
        comm_call = next(args for signature, args in self.control.calls if signature.startswith("CommRqData"))
        self.assertEqual("opw00001", comm_call[1])
        self.assertEqual("9103", comm_call[3])

        self.control.summary = {"예수금": " +001,250,000 ", "주문가능금액": "000920000"}
        self.control.server_gubun = "1"
        self.api._on_receive_tr_data("9103", requested["rqname"], "opw00001", "", "0")

        self.assertEqual(1, len(results))
        self.assertEqual(" +001,250,000 ", results[0]["raw_deposit"])
        self.assertEqual("000920000", results[0]["raw_orderable_cash"])
        self.assertEqual("SIMULATION", results[0]["account_type"])

    def test_account_funds_timeout_rejects_late_callback(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_account_funds_snapshot(
            self.account_no,
            request_id=8,
            callback=results.append,
        )
        rqname = str(requested["rqname"])
        self.api._expire_account_funds_request(rqname)
        self.assertEqual(1, len(results))
        self.assertFalse(results[0]["ok"])
        self.assertIn("timed out", results[0]["error"])

        self.control.summary = {"예수금": "100", "주문가능금액": "90"}
        self.api._on_receive_tr_data("9103", rqname, "opw00001", "", "0")
        self.assertEqual(1, len(results))

    def test_account_funds_commrq_failure_is_immediate(self) -> None:
        results: list[dict[str, object]] = []
        self.control.comm_rq_result = -200
        requested = self.api.request_account_funds_snapshot(
            self.account_no,
            request_id=9,
            callback=results.append,
        )
        self.assertFalse(requested["ok"])
        self.assertEqual(1, len(results))
        self.assertEqual({}, self.api._pending_tr)

    def test_account_password_window_uses_installed_official_koa_function(self) -> None:
        result = self.api.show_account_password_window()

        self.assertTrue(result["ok"])
        self.assertIn(
            (
                "KOA_Functions(QString, QString)",
                ("ShowAccountWindow", ""),
            ),
            self.control.calls,
        )

    def test_account_password_message_fails_request_and_emits_account_evidence(self) -> None:
        results: list[dict[str, object]] = []
        self.api.account_authentication_required = _Signal()
        requested = self.api.request_account_funds_snapshot(
            self.account_no,
            request_id=10,
            callback=results.append,
        )

        self.api._on_receive_msg(
            "9103",
            requested["rqname"],
            "opw00001",
            "(55) 계좌비밀번호 입력을 확인해주시기 바랍니다.",
        )

        self.assertEqual(1, len(results))
        self.assertFalse(results[0]["ok"])
        self.assertEqual(
            self.module.ACCOUNT_AUTHENTICATION_REQUIRED,
            results[0]["error_kind"],
        )
        self.assertEqual(
            self.account_no,
            self.api.account_authentication_required.values[0]["account_id"],
        )
        self.assertNotIn(requested["rqname"], self.api._pending_tr)

    def test_verified_numeric_account_errors_are_auth_evidence(self) -> None:
        self.assertTrue(self.module.account_authentication_required_message("(5)"))
        self.assertTrue(self.module.account_authentication_required_message("(55)"))
        self.assertFalse(self.module.account_authentication_required_message("(56)"))
        self.assertTrue(
            self.module.account_authentication_required_message(
                "비밀번호 입력을 확인해주시기 바랍니다."
            )
        )

    def test_verified_numeric_funds_field_is_projected_as_auth_required(self) -> None:
        results: list[dict[str, object]] = []
        self.api.account_authentication_required = _Signal()
        requested = self.api.request_account_funds_snapshot(
            self.account_no,
            request_id=11,
            callback=results.append,
        )
        self.control.summary = {"예수금": "(5)", "주문가능금액": "(55)"}

        self.api._on_receive_tr_data(
            "9103",
            requested["rqname"],
            "opw00001",
            "",
            "0",
        )

        self.assertEqual(1, len(results))
        self.assertFalse(results[0]["ok"])
        self.assertEqual(
            self.module.ACCOUNT_AUTHENTICATION_REQUIRED,
            results[0]["error_kind"],
        )
        self.assertEqual(1, len(self.api.account_authentication_required.values))


if __name__ == "__main__":
    unittest.main()
