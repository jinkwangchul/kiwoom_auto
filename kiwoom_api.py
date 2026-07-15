# -*- coding: utf-8 -*-
"""Data-query-only Kiwoom OpenAPI wrapper.

This first wrapper only supports login status checks and opt10080 minute candle
queries. It does not place orders, register realtime feeds, call the routine
engine, or write rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

try:
    from PyQt5.QAxContainer import QAxWidget
except Exception as exc:  # pragma: no cover - depends on Windows/COM runtime.
    QAxWidget = None
    _QAX_IMPORT_ERROR: Exception | None = exc
else:
    _QAX_IMPORT_ERROR = None

from kiwoom_candle_adapter import save_minute_candles_for_stock


Opt10080Callback = Callable[[dict[str, Any]], None]


class KiwoomApi(QObject):
    """Minimal Kiwoom API wrapper for opt10080 candle lookup."""

    login_state_changed = pyqtSignal(dict)

    CONTROL_NAME = "KHOPENAPI.KHOpenAPICtrl.1"
    OPT10080_FIELDS = ("체결시간", "시가", "고가", "저가", "현재가", "거래량")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._control: Any = None
        self._available = False
        self._unavailable_reason = ""
        self._connected = False
        self._login_requested = False
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

    def _on_event_connect(self, err_code: Any) -> None:
        try:
            code = int(err_code)
        except (TypeError, ValueError):
            code = -9999

        self.last_login_error = code
        self._connected = code == 0
        if code == 0:
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
        self.last_login_message = messages.get(code, f"login failed: {code}")
        self.login_state_changed.emit(
            {
                "connected": False,
                "err_code": code,
                "message": self.last_login_message,
            }
        )

    def _on_receive_tr_data(self, *args: Any) -> None:
        if len(args) < 5:
            return
        _screen_no, rqname, trcode, _record_name, prev_next = args[:5]
        pending = self._pending_tr.pop(str(rqname), None)
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
    def _finish_callback(callback: Opt10080Callback | None, result: dict[str, Any]) -> dict[str, Any]:
        if callable(callback):
            try:
                callback(result)
            except Exception as exc:
                result = dict(result)
                result["callback_error"] = str(exc)
        return result
