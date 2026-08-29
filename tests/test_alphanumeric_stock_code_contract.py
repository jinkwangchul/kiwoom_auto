from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import QObject

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from event_journal_writer import EventJournalWriter
import gui_auto_trade_setting_window as setting_window
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
)
from kiwoom_api import KiwoomApi
from kiwoom_send_order_safety_gate import evaluate_kiwoom_send_order_safety
from kiwoom_stock_library_service import (
    KiwoomStockLibrarySyncService,
    StockLibrarySyncState,
    validate_stock_library_records,
)
from performance_aggregator import CanonicalPerformanceAggregator
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    CanonicalStockPerformanceLedgerRepository,
)
from stock_code_contract import (
    is_broker_action_stock_code,
    is_valid_stock_code,
    normalize_broker_stock_code,
    normalize_stock_code,
)
from stock_repository import StockRepository


CODE = "00088K"
T0 = "2026-08-23T09:00:00+09:00"


class AlphanumericStockCodeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_canonical_examples_and_invalid_boundaries(self) -> None:
        for value, expected in (
            ("005930", "005930"),
            ("00088K", "00088K"),
            ("0000D0", "0000D0"),
            (" 00088k ", "00088K"),
        ):
            self.assertEqual(expected, normalize_stock_code(value))
            self.assertTrue(is_valid_stock_code(value))
        for value in ("12345", "1234567", "12-34A", "000000", "ABCDEF"):
            self.assertFalse(is_valid_stock_code(value), value)
        self.assertEqual("005930", normalize_broker_stock_code("A005930"))
        self.assertEqual("A12345", normalize_broker_stock_code("A12345"))
        self.assertEqual("0134X0", normalize_broker_stock_code("A0134X0"))

    def test_mixed_4302_master_records_all_validate(self) -> None:
        numeric = [f"{index:06d}" for index in range(1, 3927)]
        alpha_tail = [f"{index:04d}A{index % 10}" for index in range(1, 354)]
        alpha_last = [f"{index:05d}B" for index in range(1, 24)]
        codes = numeric + alpha_tail + alpha_last
        records = [
            {
                "code": code.lower() if "A" in code else code,
                "name": f"종목{index}",
                "market": "KOSPI" if index < 3600 else "KOSDAQ",
            }
            for index, code in enumerate(codes)
        ]
        validated, diagnostics = validate_stock_library_records(
            records,
            market_raw_counts={"KOSPI": 3600, "KOSDAQ": len(records) - 3600},
            raw_code_count=len(records),
            name_lookup_count=len(records),
            failed_name_count=0,
        )
        self.assertEqual(4302, len(validated))
        self.assertEqual(1.0, diagnostics["valid_code_ratio"])
        self.assertEqual(376, sum(not item["code"].isdigit() for item in validated))

    def test_repository_episode_ledger_and_aggregate_preserve_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stock_repository = StockRepository(root)
            stock_dir = stock_repository.ensure_stock_folder(CODE.lower(), "영숫자종목")
            self.assertEqual(f"{CODE}_영숫자종목", stock_dir.name)
            self.assertEqual(CODE, stock_repository.find_by_code(CODE).code)

            episodes = CanonicalAssignmentEpisodeRepository(root)
            opened = episodes.open_episode(
                CODE,
                AssignmentEpisodeTarget.unassigned(),
                started_at=T0,
                start_reason="STOCK_REGISTERED",
                source="TEST",
            )
            self.assertTrue(opened.success, opened.error)
            episode = opened.opened_episode
            self.assertEqual(CODE, episodes.list_episodes(CODE)[0].stock_code)

            ledger = CanonicalStockPerformanceLedgerRepository(
                root,
                episode_repository=episodes,
                now_factory=lambda: datetime.fromisoformat("2026-08-23T10:01:00+09:00"),
            )
            result = ledger.append_event(
                {
                    "stock_code": CODE.lower(),
                    "broker": "KIWOOM",
                    "account_number": "1234-5678",
                    "trade_date": "2026-08-23",
                    "broker_order_no": "ORDER-A",
                    "execution_identity": "EXEC-A",
                    "fill_id": "FILL-A",
                    "realization_id": "REALIZATION-A",
                    "realized_at": "2026-08-23T10:00:00+09:00",
                    "quantity": 1,
                    "realized_cost_basis": 100,
                    "gross_pnl": 10,
                    "fee": 0,
                    "tax": 0,
                    "net_pnl": 10,
                    "exit_episode_id": episode.episode_id,
                    "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                    "allocations": [
                        {
                            "entry_lot_id": "LOT-A",
                            "entry_episode_id": episode.episode_id,
                            "quantity": 1,
                            "cost_basis": 100,
                            "gross_pnl": 10,
                            "net_pnl": 10,
                        }
                    ],
                }
            )
            self.assertTrue(result.success, result.error)
            self.assertEqual(CODE, ledger.list_events(CODE)[0].stock_code)
            aggregate = CanonicalPerformanceAggregator(ledger, episodes).aggregate_stock_lifetime(CODE)
            self.assertEqual(CODE, aggregate.stock_code)
            self.assertEqual(10, aggregate.gross_pnl)

    def test_event_journal_preserves_alphanumeric_stock_code(self) -> None:
        with TemporaryDirectory() as tmp:
            writer = EventJournalWriter(Path(tmp), event_id_factory=lambda: "EVENT-ALPHA")
            result = writer.append_event(
                event_type="INTEGRITY_WARNING",
                occurred_at="2026-08-23T10:00:00+09:00",
                category="SYSTEM",
                severity="WARNING",
                template_args={"target": "영숫자 종목"},
                stock_code=CODE,
                stock_name="영숫자종목",
            )
            self.assertTrue(result["appended"], result)
            payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(CODE, payload["stock_code"])

    def test_broker_boundaries_fail_closed_without_dynamic_call(self) -> None:
        self.assertFalse(is_broker_action_stock_code(CODE))
        control = MagicMock()
        api = KiwoomApi.__new__(KiwoomApi)
        QObject.__init__(api)
        api._control = control
        api._available = True
        api._connected = True
        api._unavailable_reason = ""
        api._login_session_id = "SESSION"
        api._connection_epoch = 1
        api._login_requested = False
        api.last_login_error = 0
        api.last_login_message = "connected"
        api._realtime_shadow_registration = api._empty_realtime_shadow_snapshot()

        with self.assertRaisesRegex(ValueError, "UNCONFIRMED"):
            api.send_order("0101", "BUY", "12345678", 1, CODE, 1, 0, "03", "")
        candle = api.request_minute_candles(CODE)
        self.assertFalse(candle["ok"])
        self.assertEqual("BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED", candle["reason_code"])
        realtime = api.sync_realtime_shadow_registration([CODE])
        self.assertFalse(realtime["ok"])
        self.assertEqual("BROKER_ALPHANUMERIC_STOCK_CODE_UNCONFIRMED", realtime["reason_code"])
        control.dynamicCall.assert_not_called()

    def test_send_order_safety_gate_blocks_alphanumeric_identity(self) -> None:
        params = {
            "screen_no": "0101",
            "order_name": "BUY",
            "account_no": "12345678",
            "order_type": 1,
            "code": CODE,
            "quantity": 1,
            "price": 0,
            "hoga": "03",
            "original_order_no": "",
        }
        result = evaluate_kiwoom_send_order_safety(
            {
                "status": "SEND_ORDER_CONTRACT_READY",
                "send_order_adapter_contract": {
                    "dispatch_id": "D1",
                    "order_id": "O1",
                    "account_no": "12345678",
                    "screen_no": "0101",
                    "send_order_params": dict(params),
                },
                "send_order_params": params,
                "send_order_called": False,
                "broker_called": False,
            },
            {"locks": [], "existing_dispatches": [], "emergency_stop": False},
            {"kiwoom_connected": True, "account_no": "12345678"},
            {"operator_final_send_confirmed": True, "emergency_stop": False},
        )
        self.assertEqual("INVALID", result["status"])
        self.assertIn("broker support is unconfirmed", result["issues"][0])

    def test_search_supports_code_name_chosung_and_reuses_same_target_dialog(self) -> None:
        library = (
            {"code": CODE, "name": "고려영숫자", "market": "KOSPI", "chosung": "ㄱㄹㅇㅅㅈ"},
            {"code": "005930", "name": "삼성전자", "market": "KOSPI", "chosung": "ㅅㅅㅈㅈ"},
        )
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            library,
        )
        owner = QWidget()
        self.addCleanup(owner.close)
        with (
            patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
        ):
            first = setting_window.open_instance_stock_search_register_dialog(
                owner,
                {"instance_id": "INSTANCE-A", "instance_name": "A"},
            )
            self.addCleanup(first.close)
            second = setting_window.open_instance_stock_search_register_dialog(
                owner,
                {"instance_id": "INSTANCE-A", "instance_name": "A"},
            )
            other = setting_window.open_instance_stock_search_register_dialog(
                owner,
                {"instance_id": "INSTANCE-B", "instance_name": "B"},
            )
            self.addCleanup(other.close)
            self.assertIs(first, second)
            self.assertIsNot(first, other)
            for keyword in (CODE.lower(), "고려", "ㄱㄹ"):
                first.search_input.setText(keyword)
                first.search_stocks()
                self.assertEqual(1, first.result_table.rowCount(), keyword)
                self.assertEqual(CODE, first.result_table.item(0, 0).text())

    def test_diagnostic_file_name_uses_epoch_and_full_session_hash(self) -> None:
        api = MagicMock()
        with TemporaryDirectory() as tmp:
            service = KiwoomStockLibrarySyncService(api, project_root=Path(tmp))
            service.state = StockLibrarySyncState(
                session_id="KIWOOM-LOGIN-SESSION-ONE",
                connection_epoch=7,
            )
            first = service._diagnostic_file_path().name
            service.state = StockLibrarySyncState(
                session_id="KIWOOM-LOGIN-SESSION-TWO",
                connection_epoch=7,
            )
            second = service._diagnostic_file_path().name
            self.assertNotEqual(first, second)
            self.assertTrue(first.startswith("stock_library_invalid_codes_e7_"))
            self.assertNotIn("SESSION", first)


if __name__ == "__main__":
    unittest.main()
