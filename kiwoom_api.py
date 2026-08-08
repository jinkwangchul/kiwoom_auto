# -*- coding: utf-8 -*-
"""Data-query-only Kiwoom OpenAPI wrapper.

This first wrapper only supports login status checks and opt10080 minute candle
queries. It does not place orders, register realtime feeds, call the routine
engine, or write rules.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

try:
    from PyQt5.QAxContainer import QAxWidget
except Exception as exc:  # pragma: no cover - depends on Windows/COM runtime.
    QAxWidget = None
    _QAX_IMPORT_ERROR: Exception | None = exc
else:
    _QAX_IMPORT_ERROR = None

from kiwoom_candle_adapter import save_minute_candles_for_stock
from kiwoom_trade_cost_diagnostic import record_trade_cost_chejan_diagnostic
from production_recovery_contract import RecoverySessionIdentity, build_snapshot_part


Opt10080Callback = Callable[[dict[str, Any]], None]
RecoverySnapshotCallback = Callable[[dict[str, Any]], None]
AccountFundsCallback = Callable[[dict[str, Any]], None]

TRADE_COST_DIAGNOSTIC_FIDS = (
    "9201",
    "9203",
    "904",
    "9001",
    "302",
    "907",
    "900",
    "901",
    "910",
    "911",
    "902",
    "903",
    "913",
    "908",
    "938",
    "939",
)


class KiwoomApi(QObject):
    """Minimal Kiwoom API wrapper for opt10080 candle lookup."""

    login_state_changed = pyqtSignal(dict)
    raw_chejan_received = pyqtSignal(dict)

    CONTROL_NAME = "KHOPENAPI.KHOpenAPICtrl.1"
    OPT10080_FIELDS = ("체결시간", "시가", "고가", "저가", "현재가", "거래량")
    OPW00018_FIELDS = (
        "종목번호",
        "종목명",
        "평가손익",
        "수익률(%)",
        "매입가",
        "보유수량",
        "매매가능수량",
        "현재가",
        "평가금액",
    )
    OPT10075_FIELDS = (
        "계좌번호",
        "주문번호",
        "종목코드",
        "주문상태",
        "종목명",
        "주문수량",
        "주문가격",
        "미체결수량",
        "원주문번호",
        "주문구분",
        "매매구분",
        "시간",
        "체결량",
    )
    HOLDINGS_SCREEN_NO = "9101"
    OPEN_ORDERS_SCREEN_NO = "9102"
    ACCOUNT_FUNDS_SCREEN_NO = "9103"
    RECOVERY_TR_TIMEOUT_MS = 15_000
    ACCOUNT_FUNDS_TR_TIMEOUT_MS = 15_000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._control: Any = None
        self._available = False
        self._unavailable_reason = ""
        self._connected = False
        self._login_requested = False
        self._login_session_id = ""
        self.last_login_error: int | None = None
        self.last_login_message = "login not requested"
        self._pending_tr: dict[str, dict[str, Any]] = {}

        if QAxWidget is None:
            self._unavailable_reason = f"QAxContainer import failed: {_QAX_IMPORT_ERROR}"
            return
        if QApplication.instance() is None:
            self._unavailable_reason = "QApplication is required before creating QAxWidget"
            return

        try:
            control = QAxWidget(parent)
            if not control.setControl(self.CONTROL_NAME):
                self._unavailable_reason = f"control unavailable: {self.CONTROL_NAME}"
                self._control = control
                return
            control.OnEventConnect.connect(self._on_event_connect)
            control.OnReceiveTrData.connect(self._on_receive_tr_data)
            chejan_signal = getattr(control, "OnReceiveChejanData", None)
            if chejan_signal is not None:
                chejan_signal.connect(self._on_receive_chejan_data)
            self._control = control
            self._available = True
        except Exception as exc:  # pragma: no cover - depends on Kiwoom OCX.
            self._control = None
            self._available = False
            self._unavailable_reason = str(exc)

    def is_available(self) -> bool:
        return bool(self._available and self._control is not None)

    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def login(self) -> dict[str, Any]:
        if not self.is_available():
            self._connected = False
            self._login_requested = False
            self.last_login_error = None
            self.last_login_message = self._unavailable_reason or "kiwoom api unavailable"
            return {
                "ok": False,
                "status": "unavailable",
                "connected": False,
                "error": self.last_login_message,
            }
        try:
            self._login_requested = True
            self._login_session_id = ""
            self.last_login_error = None
            self.last_login_message = "login requested"
            result = self._control.dynamicCall("CommConnect()")
            if int(result or 0) != 0:
                self._connected = False
                self.last_login_error = int(result or -1)
                self.last_login_message = "login request failed"
                return {
                    "ok": False,
                    "status": "login_request_failed",
                    "connected": False,
                    "result": result,
                    "error": self.last_login_message,
                }
            return {
                "ok": True,
                "status": "login_requested",
                "connected": self.is_connected(),
                "result": result,
                "message": self.last_login_message,
            }
        except Exception as exc:
            self._connected = False
            self.last_login_error = None
            self.last_login_message = str(exc)
            return {
                "ok": False,
                "status": "login_request_failed",
                "connected": False,
                "error": self.last_login_message,
            }

    def is_connected(self) -> bool:
        if not self.is_available():
            self._connected = False
            return False
        try:
            self._connected = int(self._control.dynamicCall("GetConnectState()") or 0) == 1
            return self._connected
        except Exception:
            return bool(self._connected)

    def account_numbers(self) -> list[str]:
        """Return Kiwoom login account numbers from the active OpenAPI session."""
        if not self.is_available() or not self.is_connected():
            return []

        try:
            raw_value = self._control.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        except Exception:
            return []

        accounts: list[str] = []
        seen: set[str] = set()
        for item in str(raw_value or "").split(";"):
            account = item.strip()
            if not account or account in seen:
                continue
            accounts.append(account)
            seen.add(account)
        return accounts

    def account_server_type(self) -> str:
        """Return the official login-server classification for UI projection."""
        if not self.is_available() or not self.is_connected():
            return ""
        try:
            raw_value = self._control.dynamicCall(
                "GetLoginInfo(QString)",
                "GetServerGubun",
            )
        except Exception:
            return ""
        value = str(raw_value or "").strip()
        if value == "1":
            return "SIMULATION"
        return "REAL" if value else ""

    def login_session_id(self) -> str:
        """Return the current process-local Kiwoom login session identity."""
        return self._login_session_id if self.is_connected() else ""

    def send_order(
        self,
        screen_no: str,
        order_name: str,
        account_no: str,
        order_type: int,
        code: str,
        quantity: int,
        price: int,
        hoga: str,
        original_order_no: str,
    ) -> Any:
        """Call Kiwoom OpenAPI SendOrder once with the official 9 arguments."""
        if not self.is_available():
            raise RuntimeError(self._unavailable_reason or "kiwoom api unavailable")
        if not self.is_connected():
            raise RuntimeError("kiwoom api is not connected")
        return self._control.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            str(screen_no or ""),
            str(order_name or ""),
            str(account_no or ""),
            int(order_type),
            str(code or ""),
            int(quantity),
            int(price),
            str(hoga or ""),
            str(original_order_no or ""),
        )

    def request_minute_candles(
        self,
        code: str,
        name: str = "",
        interval: int = 1,
        count: int = 300,
        screen_no: str = "9001",
        callback: Opt10080Callback | None = None,
    ) -> dict[str, Any]:
        """Request opt10080 minute candles and save the response on receipt."""
        clean_code = str(code or "").strip()
        if not clean_code:
            return self._finish_callback(
                callback,
                {"ok": False, "error": "stock code is required"},
            )
        if not self.is_available():
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "error": self._unavailable_reason or "kiwoom api unavailable"},
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "error": "kiwoom api is not connected"},
            )

        try:
            clean_interval = max(int(interval), 1)
        except (TypeError, ValueError):
            clean_interval = 1
        try:
            clean_count = max(int(count), 1)
        except (TypeError, ValueError):
            clean_count = 300

        rqname = f"opt10080_{clean_code}_{datetime.now().strftime('%H%M%S%f')}"
        self._pending_tr[rqname] = {
            "type": "minute_candles",
            "code": clean_code,
            "name": str(name or "").strip(),
            "interval": clean_interval,
            "count": clean_count,
            "screen_no": str(screen_no or "9001"),
            "callback": callback,
            "rows": [],
        }

        try:
            self._control.dynamicCall("SetInputValue(QString, QString)", "종목코드", clean_code)
            self._control.dynamicCall("SetInputValue(QString, QString)", "틱범위", str(clean_interval))
            self._control.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")
            result = self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                "opt10080",
                0,
                str(screen_no or "9001"),
            )
        except Exception as exc:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "rqname": rqname, "error": str(exc)},
            )

        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "code": clean_code, "rqname": rqname, "result": result},
            )

        return {"ok": True, "code": clean_code, "rqname": rqname, "result": result}

    def request_account_holdings_snapshot(
        self,
        identity: RecoverySessionIdentity,
        *,
        screen_no: str = HOLDINGS_SCREEN_NO,
        callback: RecoverySnapshotCallback | None = None,
        timeout_ms: int = RECOVERY_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request a complete OPW00018 holdings snapshot without writing Runtime."""
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        return self._start_recovery_snapshot_request(
            identity,
            kind="HOLDINGS",
            trcode="opw00018",
            screen_no=screen_no,
            callback=callback,
            timeout_ms=timeout_ms,
            inputs=(
                ("계좌번호", identity.account_no),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "1"),
                ("거래소구분", ""),
            ),
        )

    def request_open_orders_snapshot(
        self,
        identity: RecoverySessionIdentity,
        *,
        screen_no: str = OPEN_ORDERS_SCREEN_NO,
        callback: RecoverySnapshotCallback | None = None,
        timeout_ms: int = RECOVERY_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request a complete OPT10075 open-order snapshot without mutation."""
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        return self._start_recovery_snapshot_request(
            identity,
            kind="OPEN_ORDERS",
            trcode="opt10075",
            screen_no=screen_no,
            callback=callback,
            timeout_ms=timeout_ms,
            inputs=(
                ("계좌번호", identity.account_no),
                ("전체종목구분", "0"),
                ("매매구분", "0"),
                ("종목코드", ""),
                ("체결구분", "1"),
                ("거래소구분", "0"),
            ),
        )

    def request_account_funds_snapshot(
        self,
        account_id: str,
        *,
        request_id: int,
        screen_no: str = ACCOUNT_FUNDS_SCREEN_NO,
        callback: AccountFundsCallback | None = None,
        timeout_ms: int = ACCOUNT_FUNDS_TR_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Request OPW00001 summary values without persisting broker data."""
        clean_account = str(account_id or "").strip()
        if not self.is_available():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": self._unavailable_reason or "kiwoom api unavailable"},
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": "kiwoom api is not connected"},
            )
        if not clean_account or clean_account not in self.account_numbers():
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "error": "account is not in the login session"},
            )

        rqname = "OPW00001_ACCOUNT_FUNDS_{}_{}".format(
            int(request_id),
            datetime.now().strftime("%H%M%S%f"),
        )
        pending = {
            "type": "account_funds",
            "trcode": "opw00001",
            "screen_no": str(screen_no),
            "account_id": clean_account,
            "request_id": int(request_id),
            "callback": callback,
            "started_at": datetime.now().isoformat(timespec="microseconds"),
        }
        self._pending_tr[rqname] = pending
        try:
            for field, value in (
                ("계좌번호", clean_account),
                ("비밀번호", ""),
                ("비밀번호입력매체구분", "00"),
                ("조회구분", "2"),
            ):
                self._control.dynamicCall(
                    "SetInputValue(QString, QString)",
                    field,
                    value,
                )
            result = self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                "opw00001",
                0,
                str(screen_no),
            )
        except Exception:
            result = -1
        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {"ok": False, "account_id": clean_account, "request_id": request_id,
                 "rqname": rqname, "result": result, "error": "CommRqData failed"},
            )

        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.ACCOUNT_FUNDS_TR_TIMEOUT_MS
        QTimer.singleShot(
            clean_timeout,
            lambda request_name=rqname: self._expire_account_funds_request(request_name),
        )
        return {
            "ok": True,
            "status": "REQUESTED",
            "account_id": clean_account,
            "request_id": int(request_id),
            "rqname": rqname,
            "result": result,
        }

    def _expire_account_funds_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "account_funds":
            return
        callback = pending.get("callback")
        self._finish_callback(
            callback if callable(callback) else None,
            {
                "ok": False,
                "account_id": pending.get("account_id", ""),
                "request_id": pending.get("request_id"),
                "rqname": str(rqname),
                "error": "account funds request timed out",
            },
        )

    def _start_recovery_snapshot_request(
        self,
        identity: RecoverySessionIdentity,
        *,
        kind: str,
        trcode: str,
        screen_no: str,
        callback: RecoverySnapshotCallback | None,
        timeout_ms: int,
        inputs: tuple[tuple[str, str], ...],
    ) -> dict[str, Any]:
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery identity is required"},
            )
        if not self.is_available():
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "is_complete": False,
                    "error": self._unavailable_reason or "kiwoom api unavailable",
                },
            )
        if not self.is_connected():
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "kiwoom api is not connected"},
            )
        if identity.account_no not in self.account_numbers():
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery account is not in the login session"},
            )
        if (
            not identity.login_session_id
            or identity.login_session_id != self.login_session_id()
        ):
            return self._finish_callback(
                callback,
                {"ok": False, "is_complete": False, "error": "recovery login session is stale"},
            )

        rqname = "{}_RECOVERY_{}".format(
            trcode.upper(),
            datetime.now().strftime("%H%M%S%f"),
        )
        pending = {
            "type": "recovery_snapshot",
            "kind": str(kind),
            "trcode": str(trcode),
            "screen_no": str(screen_no),
            "callback": callback,
            "identity": identity,
            "inputs": tuple(inputs),
            "rows": [],
            "pages": 0,
            "started_at": datetime.now().isoformat(timespec="microseconds"),
        }
        self._pending_tr[rqname] = pending
        result = self._submit_recovery_snapshot_page(rqname, pending, prev_next=0)
        if int(result or 0) != 0:
            self._pending_tr.pop(rqname, None)
            return self._finish_callback(
                callback,
                {
                    "ok": False,
                    "is_complete": False,
                    "rqname": rqname,
                    "result": result,
                    "error": "CommRqData failed",
                },
            )
        try:
            clean_timeout = max(int(timeout_ms), 1)
        except (TypeError, ValueError):
            clean_timeout = self.RECOVERY_TR_TIMEOUT_MS
        QTimer.singleShot(
            clean_timeout,
            lambda request_name=rqname: self._expire_recovery_snapshot_request(request_name),
        )
        return {
            "ok": True,
            "is_complete": False,
            "status": "REQUESTED",
            "kind": kind,
            "account_no": identity.account_no,
            "trading_day": identity.trading_day,
            "recovery_session_id": identity.recovery_session_id,
            "rqname": rqname,
            "result": result,
        }

    def _submit_recovery_snapshot_page(
        self,
        rqname: str,
        pending: dict[str, Any],
        *,
        prev_next: int,
    ) -> Any:
        try:
            for field, value in pending.get("inputs", ()):
                self._control.dynamicCall(
                    "SetInputValue(QString, QString)",
                    str(field),
                    str(value),
                )
            return self._control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                str(rqname),
                str(pending.get("trcode") or ""),
                int(prev_next),
                str(pending.get("screen_no") or ""),
            )
        except Exception:
            return -1

    def _expire_recovery_snapshot_request(self, rqname: str) -> None:
        pending = self._pending_tr.pop(str(rqname), None)
        if not pending or pending.get("type") != "recovery_snapshot":
            return
        self._finish_recovery_snapshot(
            str(rqname),
            pending,
            collection_complete=False,
            errors=("broker snapshot request timed out",),
        )

    def _on_event_connect(self, err_code: Any) -> None:
        try:
            code = int(err_code)
        except (TypeError, ValueError):
            code = -9999

        self.last_login_error = code
        self._connected = code == 0
        if code == 0:
            connected_at = datetime.now().isoformat(timespec="microseconds")
            account_payload = ";".join(self.account_numbers())
            digest = hashlib.sha256(
                f"{connected_at}|{account_payload}".encode("utf-8")
            ).hexdigest().upper()
            self._login_session_id = f"KIWOOM_LOGIN_SESSION_{digest}"
            self.last_login_message = "login succeeded"
            self.login_state_changed.emit(
                {
                    "connected": True,
                    "err_code": code,
                    "message": self.last_login_message,
                }
            )
            return

        messages = {
            -100: "user info exchange failed",
            -101: "server connection failed",
            -102: "version processing failed",
        }
        self._login_session_id = ""
        self.last_login_message = messages.get(code, f"login failed: {code}")
        self.login_state_changed.emit(
            {
                "connected": False,
                "err_code": code,
                "message": self.last_login_message,
            }
        )

    def _on_receive_chejan_data(self, gubun: Any, item_cnt: Any, fid_list: Any) -> None:
        if not self.is_available():
            return
        fids: list[str] = []
        for item in str(fid_list or "").split(";"):
            fid = item.strip()
            if fid:
                fids.append(fid)

        observed_fids = list(dict.fromkeys([*fids, *TRADE_COST_DIAGNOSTIC_FIDS]))
        fid_raw_values: dict[str, str] = {}
        fid_values: dict[str, str] = {}
        for fid in observed_fids:
            try:
                value = self._control.dynamicCall("GetChejanData(int)", int(fid))
            except Exception:
                value = ""
            raw_text = "" if value is None else str(value)
            fid_raw_values[fid] = raw_text
            fid_values[fid] = raw_text.strip()

        try:
            count = int(item_cnt)
        except (TypeError, ValueError):
            count = len(fids)

        raw_event = {
            "source": "kiwoom_chejan",
            "gubun": str(gubun or "").strip(),
            "item_count": count,
            "fid_list": fids,
            "observed_fid_list": observed_fids,
            "fid_raw_values": fid_raw_values,
            "fid_values": fid_values,
            "received_at": datetime.now().isoformat(sep=" ", timespec="milliseconds"),
        }
        if raw_event["gubun"] == "0":
            try:
                record_trade_cost_chejan_diagnostic(raw_event)
            except Exception:
                pass
        self.raw_chejan_received.emit(raw_event)

    def _on_receive_tr_data(self, *args: Any) -> None:
        if len(args) < 5:
            return
        _screen_no, rqname, trcode, _record_name, prev_next = args[:5]
        request_name = str(rqname)
        pending = self._pending_tr.get(request_name)
        if not pending:
            return
        if pending.get("type") == "recovery_snapshot":
            self._on_receive_recovery_snapshot_page(
                request_name,
                str(trcode),
                str(prev_next).strip(),
                pending,
            )
            return
        if pending.get("type") == "account_funds":
            self._on_receive_account_funds(request_name, str(trcode), pending)
            return
        pending = self._pending_tr.pop(request_name, None)
        if not pending or pending.get("type") != "minute_candles":
            return

        callback = pending.get("callback")
        try:
            rows = self._read_opt10080_rows(str(trcode), str(rqname), int(pending.get("count") or 300))
            pending["rows"] = rows
            saved = save_minute_candles_for_stock(
                str(pending.get("code", "")),
                str(pending.get("name", "")),
                rows,
                max_count=int(pending.get("count") or 300),
            )
            result = {
                "ok": True,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "rows_count": len(rows),
                "saved_count": len(saved),
                "has_more": str(prev_next).strip() == "2",
                "warning": "additional pages available" if str(prev_next).strip() == "2" else "",
            }
        except Exception as exc:
            result = {
                "ok": False,
                "type": "minute_candles",
                "code": pending.get("code", ""),
                "name": pending.get("name", ""),
                "rqname": str(rqname),
                "trcode": str(trcode),
                "error": str(exc),
            }

        self._finish_callback(callback if callable(callback) else None, result)

    def _on_receive_account_funds(
        self,
        rqname: str,
        trcode: str,
        pending: dict[str, Any],
    ) -> None:
        current = self._pending_tr.pop(rqname, None)
        if current is not pending:
            return
        callback = pending.get("callback")
        try:
            raw_deposit = self._control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                trcode,
                rqname,
                0,
                "예수금",
            )
            raw_orderable = self._control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                trcode,
                rqname,
                0,
                "주문가능금액",
            )
            result = {
                "ok": True,
                "account_id": pending.get("account_id", ""),
                "request_id": pending.get("request_id"),
                "rqname": rqname,
                "raw_deposit": str(raw_deposit or ""),
                "raw_orderable_cash": str(raw_orderable or ""),
                "account_type": self.account_server_type(),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as exc:
            result = {
                "ok": False,
                "account_id": pending.get("account_id", ""),
                "request_id": pending.get("request_id"),
                "rqname": rqname,
                "error": f"account funds response parsing failed: {exc}",
            }
        self._finish_callback(callback if callable(callback) else None, result)

    def _on_receive_recovery_snapshot_page(
        self,
        rqname: str,
        trcode: str,
        prev_next: str,
        pending: dict[str, Any],
    ) -> None:
        try:
            kind = str(pending.get("kind") or "")
            fields = (
                self.OPW00018_FIELDS
                if kind == "HOLDINGS"
                else self.OPT10075_FIELDS
            )
            rows = self._read_tr_rows(trcode, rqname, fields)
            pending.setdefault("rows", []).extend(rows)
            pending["pages"] = int(pending.get("pages") or 0) + 1
        except Exception as exc:
            self._pending_tr.pop(rqname, None)
            self._finish_recovery_snapshot(
                rqname,
                pending,
                collection_complete=False,
                errors=(f"broker snapshot response parsing failed: {exc}",),
            )
            return

        if prev_next == "2":
            result = self._submit_recovery_snapshot_page(
                rqname,
                pending,
                prev_next=2,
            )
            if int(result or 0) == 0:
                return
            self._pending_tr.pop(rqname, None)
            self._finish_recovery_snapshot(
                rqname,
                pending,
                collection_complete=False,
                errors=("broker snapshot continuation request failed",),
            )
            return

        self._pending_tr.pop(rqname, None)
        self._finish_recovery_snapshot(
            rqname,
            pending,
            collection_complete=True,
        )

    def _finish_recovery_snapshot(
        self,
        rqname: str,
        pending: dict[str, Any],
        *,
        collection_complete: bool,
        errors: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        identity = pending.get("identity")
        callback = pending.get("callback")
        if not isinstance(identity, RecoverySessionIdentity):
            return self._finish_callback(
                callback if callable(callback) else None,
                {
                    "ok": False,
                    "is_complete": False,
                    "rqname": rqname,
                    "error": "recovery identity was lost",
                },
            )
        part = build_snapshot_part(
            identity=identity,
            kind=str(pending.get("kind") or ""),
            rows=pending.get("rows") or (),
            completed_at=datetime.now().isoformat(timespec="microseconds"),
            source="KIWOOM_OPENAPI_TR",
            collection_complete=collection_complete,
            collection_errors=errors,
        )
        result = {
            "ok": part.is_complete,
            "is_complete": part.is_complete,
            "status": "COMPLETED" if part.is_complete else "INCOMPLETE",
            "kind": part.kind,
            "account_no": part.account_no,
            "trading_day": part.trading_day,
            "recovery_session_id": part.recovery_session_id,
            "rqname": rqname,
            "pages": int(pending.get("pages") or 0),
            "rows_count": len(part.items),
            "snapshot": part,
            "errors": list(part.errors),
        }
        return self._finish_callback(
            callback if callable(callback) else None,
            result,
        )

    def _read_tr_rows(
        self,
        trcode: str,
        rqname: str,
        fields: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        repeat_count = int(
            self._control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                trcode,
                rqname,
            )
            or 0
        )
        rows: list[dict[str, Any]] = []
        for index in range(repeat_count):
            row: dict[str, Any] = {}
            for field in fields:
                value = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    index,
                    field,
                )
                row[field] = str(value or "").strip()
            rows.append(row)
        return rows

    def _read_opt10080_rows(self, trcode: str, rqname: str, count: int) -> list[dict[str, Any]]:
        repeat_count = int(self._control.dynamicCall(
            "GetRepeatCnt(QString, QString)",
            trcode,
            rqname,
        ) or 0)
        limit = min(max(int(count or 0), 0), repeat_count) if count else repeat_count

        rows: list[dict[str, Any]] = []
        for index in range(limit):
            row: dict[str, Any] = {}
            for field in self.OPT10080_FIELDS:
                value = self._control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    trcode,
                    rqname,
                    index,
                    field,
                )
                row[field] = str(value or "").strip()
            rows.append(row)
        return rows

    @staticmethod
    def _finish_callback(
        callback: Callable[[dict[str, Any]], None] | None,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if callable(callback):
            try:
                callback(result)
            except Exception as exc:
                result = dict(result)
                result["callback_error"] = str(exc)
        return result
