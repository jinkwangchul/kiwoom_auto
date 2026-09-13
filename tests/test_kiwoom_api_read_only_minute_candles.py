# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_kiwoom_recovery_snapshot_adapter import (
    _Control,
    _Signal,
    _Timer,
    _load_kiwoom_api_module,
)


class KiwoomApiReadOnlyMinuteCandlesTest(unittest.TestCase):
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
        self.control = _Control("1234567890")
        self.api = self.module.KiwoomApi.__new__(self.module.KiwoomApi)
        self.api._control = self.control
        self.api._available = True
        self.api._connected = True
        self.api._login_requested = False
        self.api._login_session_id = "KIWOOM_LOGIN_SESSION_TEST"
        self.api._connection_epoch = 1
        self.api.last_login_error = 0
        self.api.last_login_message = "login succeeded"
        self.api._unavailable_reason = ""
        self.api._pending_tr = {}
        self.api._account_funds_request_accounts = {}
        self.api.bar_committed = _Signal()

    @staticmethod
    def _commit_result():
        notification = SimpleNamespace(
            to_payload=lambda: {
                "event_type": "BAR_COMMITTED",
                "stock_code": "005930",
            }
        )
        return SimpleNamespace(
            ok=True,
            changed=True,
            readback_verified=True,
            path="C:/temp/005930/candles.json",
            saved_count=2,
            canonical_content_hash="content-hash",
            commit_identity="commit-id",
            bar_key="005930:1:2026-09-13T09:01:00+09:00",
            bar_identity="bar-id",
            bar_time="2026-09-13T09:01:00+09:00",
            trade_date="2026-09-13",
            error_kind="",
            error="",
            notification=notification,
        )

    @staticmethod
    def _rows() -> list[dict[str, object]]:
        return [
            {
                "체결시간": "20260913090100",
                "시가": "70000",
                "고가": "70100",
                "저가": "69900",
                "현재가": "70050",
                "거래량": "100",
            },
            {
                "체결시간": "20260913090000",
                "시가": "69900",
                "고가": "70000",
                "저가": "69800",
                "현재가": "70000",
                "거래량": "90",
            },
            {
                "체결시간": "20260913085900",
                "시가": "69800",
                "고가": "69900",
                "저가": "69700",
                "현재가": "69900",
                "거래량": "80",
            },
        ]

    def _comm_rq_calls(self) -> list[tuple[object, ...]]:
        return [
            args
            for signature, args in self.control.calls
            if signature.startswith("CommRqData")
        ]

    def _set_input_calls(self) -> list[tuple[object, ...]]:
        return [
            args
            for signature, args in self.control.calls
            if signature.startswith("SetInputValue")
        ]

    def test_public_signatures_match_and_pending_modes_use_generic_registry(self) -> None:
        production_signature = inspect.signature(
            self.module.KiwoomApi.request_minute_candles
        )
        read_only_signature = inspect.signature(
            self.module.KiwoomApi.request_minute_candles_read_only
        )
        self.assertEqual(production_signature, read_only_signature)

        production = self.api.request_minute_candles("005930", "삼성전자")
        read_only = self.api.request_minute_candles_read_only("000660", "SK하이닉스")

        self.assertIs(
            True,
            self.api._pending_tr[str(production["rqname"])]["commit_to_production"],
        )
        self.assertIs(
            False,
            self.api._pending_tr[str(read_only["rqname"])]["commit_to_production"],
        )

    def test_read_only_pagination_returns_rows_without_commit(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_minute_candles_read_only(
            "005930",
            "삼성전자",
            interval=5,
            count=3,
            callback=results.append,
        )
        rqname = str(requested["rqname"])
        rows = self._rows()
        commit = Mock(side_effect=AssertionError("Production commit called"))
        original_submit = self.api._submit_governed_tr_request

        with patch.object(
            self.module,
            "commit_minute_candles_for_stock",
            commit,
        ), patch.object(
            self.api,
            "_read_opt10080_rows",
            side_effect=(rows[:2], rows[2:]),
        ), patch.object(
            self.api,
            "_submit_governed_tr_request",
            wraps=original_submit,
        ) as continuation:
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "2")
            self.assertEqual([], results)
            self.assertIn(rqname, self.api._pending_tr)
            continuation.assert_called_once()
            self.assertEqual(2, continuation.call_args.kwargs["prev_next"])
            self.assertIs(
                self.api._pending_tr[rqname],
                continuation.call_args.kwargs["pending"],
            )
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "0")

        commit.assert_not_called()
        self.assertEqual(1, len(results))
        result = results[0]
        self.assertTrue(result["ok"])
        self.assertEqual(rqname, result["request_id"])
        self.assertEqual("005930", result["code"])
        self.assertEqual("삼성전자", result["name"])
        self.assertEqual(5, result["interval"])
        self.assertEqual(rows, result["rows"])
        self.assertEqual(3, result["rows_count"])
        self.assertNotIn("commit", result)
        self.assertNotIn(rqname, self.api._pending_tr)
        self.assertFalse(self.api._screen_allocator.is_leased("3000"))
        self.assertEqual([], self.api.bar_committed.values)

    def test_production_final_still_commits_and_keeps_payload(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_minute_candles(
            "005930",
            "삼성전자",
            count=2,
            callback=results.append,
        )
        rqname = str(requested["rqname"])
        rows = self._rows()[:2]
        commit = Mock(return_value=self._commit_result())

        with patch.object(
            self.module,
            "commit_minute_candles_for_stock",
            commit,
        ), patch.object(
            self.api,
            "_read_opt10080_rows",
            return_value=rows,
        ):
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "0")

        commit.assert_called_once()
        self.assertEqual(rows, commit.call_args.args[2])
        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["commit_verified"])
        self.assertEqual(2, results[0]["saved_count"])
        self.assertEqual("commit-id", results[0]["commit_identity"])
        self.assertNotIn(rqname, self.api._pending_tr)
        self.assertFalse(self.api._screen_allocator.is_leased("3000"))
        self.assertEqual(1, len(self.api.bar_committed.values))

    def test_legacy_pending_without_mode_defaults_to_production_commit(self) -> None:
        results: list[dict[str, object]] = []
        rqname = "OPT10080_LEGACY"
        self.api._pending_tr[rqname] = {
            "type": "minute_candles",
            "code": "005930",
            "name": "삼성전자",
            "interval": 1,
            "count": 1,
            "rows": [],
            "callback": results.append,
            "request_connection_epoch": 1,
            "request_login_session_id": "KIWOOM_LOGIN_SESSION_TEST",
        }
        commit = Mock(return_value=self._commit_result())

        with patch.object(
            self.module,
            "commit_minute_candles_for_stock",
            commit,
        ), patch.object(
            self.api,
            "_read_opt10080_rows",
            return_value=self._rows()[:1],
        ):
            self.api._on_receive_tr_data("3000", rqname, "opt10080", "", "0")

        commit.assert_called_once()
        self.assertEqual(1, len(results))
        self.assertTrue(results[0]["commit_verified"])

    def test_read_only_keeps_screen_governor_timeout_and_adjusted_input(self) -> None:
        results: list[dict[str, object]] = []
        requested = self.api.request_minute_candles_read_only(
            "005930",
            screen_no="3005",
            callback=results.append,
        )
        rqname = str(requested["rqname"])

        self.assertTrue(requested["ok"])
        self.assertEqual("REQUESTED", requested["status"])
        self.assertEqual("3005", requested["screen_no"])
        self.assertEqual("3005", self._comm_rq_calls()[0][3])
        self.assertIn(("수정주가구분", "1"), self._set_input_calls())
        self.assertTrue(self.api._screen_allocator.is_leased("3005"))
        self.assertTrue(_Timer.callbacks)

        with patch.object(
            self.module,
            "commit_minute_candles_for_stock",
            side_effect=AssertionError("Production commit called"),
        ) as commit:
            self.api._expire_minute_candle_request(rqname)

        commit.assert_not_called()
        self.assertEqual(1, len(results))
        self.assertFalse(results[0]["ok"])
        self.assertNotIn(rqname, self.api._pending_tr)
        self.assertFalse(self.api._screen_allocator.is_leased("3005"))

    def test_broker_boundary_does_not_import_validation_gui_or_main(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "kiwoom_api.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        forbidden = (
            "routine_validation",
            "indicator_follow_validation",
            "validation_host",
            "gui_windows",
        )
        for module_name in imported_modules:
            for fragment in forbidden:
                with self.subTest(module=module_name, fragment=fragment):
                    self.assertNotIn(fragment, module_name.lower())


if __name__ == "__main__":
    unittest.main()
