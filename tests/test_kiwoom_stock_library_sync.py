from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_auto_trade_setting_window as setting_window
from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    STOCK_LIBRARY_SYNCING,
    StockLibraryLoadSnapshot,
    load_stock_library,
    load_stock_library_snapshot,
)
from kiwoom_api import KiwoomApi
from kiwoom_stock_library_service import (
    KiwoomStockLibrarySyncService,
    StockLibraryValidationError,
    classify_master_stock_info,
    master_stock_info_fields,
    stock_name_chosung,
    validate_stock_library_records,
)
from stock_library_master_diagnostics import build_master_code_diagnostic_projection
from event_journal_contract import EVENT_TYPE_CATEGORIES, SUMMARY_TEMPLATES


class _Scheduler:
    def __init__(self) -> None:
        self.callbacks = []

    def __call__(self, callback) -> None:
        self.callbacks.append(callback)

    def drain(self) -> None:
        while self.callbacks:
            self.callbacks.pop(0)()


class _FakeApi:
    def __init__(
        self,
        *,
        market_codes=None,
        names=None,
        raw_market_returns=None,
        stock_states=None,
        constructions=None,
        stock_infos=None,
        stock_market_kinds=None,
    ) -> None:
        self.market_codes = market_codes or {
            "0": ["005930", "000660"],
            "10": ["035720"],
            "NXT": ["005930"],
        }
        self.names = names or {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "035720": "카카오",
        }
        self.raw_market_returns = dict(raw_market_returns or {})
        self.stock_states = stock_states or {
            "005930": "정상|신용가능",
            "000660": "정상",
            "035720": "관리종목|거래정지",
        }
        self.constructions = constructions or {
            "005930": "정상",
            "000660": "투자주의",
            "035720": "투자경고",
        }
        self.stock_infos = stock_infos or {
            "005930": "시장구분0|코스피;시장구분1|중형주;업종구분|전기전자;",
            "000660": "시장구분0|코스피;시장구분1|대형주;업종구분|전기전자;",
            "035720": "시장구분0|코스닥;시장구분1|우량기업;업종구분|서비스업;",
        }
        self.stock_market_kinds = dict(stock_market_kinds or {})
        self.session_id = "SESSION-1"
        self.epoch = 1
        self.market_calls = []
        self.name_calls = []
        self.stock_state_calls = []
        self.construction_calls = []
        self.stock_info_calls = []
        self.stock_market_kind_calls = []

    def broker_readiness_snapshot(self):
        return SimpleNamespace(broker_request_ready=True, reason="READY")

    def broker_session_snapshot(self):
        return SimpleNamespace(
            login_session_id=self.session_id,
            connection_epoch=self.epoch,
        )

    def get_market_stock_codes(self, market_code):
        self.market_calls.append(market_code)
        if market_code in self.raw_market_returns:
            raw_value = self.raw_market_returns[market_code]
            values = KiwoomApi._normalize_master_code_list(raw_value)
            diagnostic = build_master_code_diagnostic_projection(raw_value)
        else:
            values = self.market_codes.get(market_code, [])
            diagnostic = None
        return {
            "ok": bool(values),
            "value": list(values),
            "diagnostic": diagnostic,
            "error": "" if values else "empty",
            "reason": "OK" if values else "MASTER_MARKET_EMPTY",
        }

    def get_master_stock_name(self, stock_code):
        self.name_calls.append(stock_code)
        value = self.names.get(stock_code, "")
        return {
            "ok": bool(value),
            "value": value,
            "error": "" if value else "empty",
            "reason": "OK" if value else "MASTER_NAME_EMPTY",
        }

    def get_master_stock_state(self, stock_code):
        self.stock_state_calls.append(stock_code)
        value = self.stock_states.get(stock_code, "")
        if isinstance(value, Exception):
            raise value
        return {"ok": True, "value": value, "error": "", "reason": "OK" if value else "EMPTY"}

    def get_master_construction(self, stock_code):
        self.construction_calls.append(stock_code)
        value = self.constructions.get(stock_code, "")
        if isinstance(value, Exception):
            return {"ok": False, "value": "", "error": str(value), "reason": "FAILED"}
        return {"ok": True, "value": value, "error": "", "reason": "OK" if value else "EMPTY"}

    def get_master_stock_info(self, stock_code):
        self.stock_info_calls.append(stock_code)
        value = self.stock_infos.get(stock_code, "")
        if isinstance(value, Exception):
            return {"ok": False, "value": "", "error": str(value), "reason": "FAILED"}
        return {"ok": True, "value": value, "error": "", "reason": "OK" if value else "EMPTY"}

    def get_master_stock_market_kind(self, stock_code):
        self.stock_market_kind_calls.append(stock_code)
        value = self.stock_market_kinds.get(stock_code, "")
        if isinstance(value, Exception):
            return {"ok": False, "value": "", "error": str(value), "reason": "FAILED"}
        return {
            "ok": bool(value),
            "value": value,
            "error": "" if value else "empty",
            "reason": "OK" if value else "EMPTY",
        }


class KiwoomStockLibrarySyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _service(self, root: Path, api=None, **kwargs):
        scheduler = _Scheduler()
        events = []

        def event_writer(event_type, **fields):
            events.append((event_type, fields))
            return {"appended": True}

        service = KiwoomStockLibrarySyncService(
            api or _FakeApi(),
            project_root=root,
            batch_size=2,
            minimum_count=kwargs.pop("minimum_count", 3),
            minimum_name_success_ratio=kwargs.pop("minimum_name_success_ratio", 0.95),
            event_writer=event_writer,
            scheduler=scheduler,
            **kwargs,
        )
        return service, scheduler, events

    @staticmethod
    def _incident_api() -> _FakeApi:
        return _FakeApi(
            raw_market_returns={
                "0": "005930; BAD01;ABC123;000000;;005930;",
                "10": "035720;1234567;12-345;\x0012345;",
            },
            names={
                "005930": "삼성전자",
                "035720": "카카오",
                "BAD01": "비대상Master",
            },
        )

    def test_normal_markets_merge_validate_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root)
            with patch.object(
                service,
                "_write_diagnostic_file",
                wraps=service._write_diagnostic_file,
            ) as diagnostic_writer:
                self.assertTrue(service.start_for_current_session())
                scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            records = json.loads(service.runtime_library_path.read_text(encoding="utf-8"))
            self.assertEqual(["000660", "005930", "035720"], [item["code"] for item in records])
            self.assertEqual("ㅅㅅㅈㅈ", records[1]["chosung"])
            self.assertEqual("정상 | 신용가능", records[1]["status"])
            self.assertEqual("정상|신용가능", records[1]["master_stock_state"])
            self.assertEqual("정상", records[1]["master_construction"])
            self.assertEqual("일반종목", records[1]["classification"])
            self.assertIn("시장구분1|중형주", records[1]["master_stock_info"])
            self.assertEqual(
                {"000660": False, "005930": True, "035720": False},
                {item["code"]: item["nxt_available"] for item in records},
            )
            metadata = json.loads(service.runtime_metadata_path.read_text(encoding="utf-8"))
            self.assertEqual("KIWOOM_OPENAPI_MASTER", metadata["source"])
            self.assertEqual("READY", metadata["sync_state"])
            self.assertEqual(3, metadata["final_count"])
            self.assertEqual(3, metadata["numeric_code_count"])
            self.assertEqual(0, metadata["alphanumeric_code_count"])
            self.assertEqual("VERIFIED", metadata["nxt_eligibility_state"])
            self.assertEqual(1, metadata["nxt_available_count"])
            self.assertEqual(3, metadata["master_stock_state_call_count"])
            self.assertEqual(3, metadata["master_construction_call_count"])
            self.assertEqual(3, metadata["master_stock_info_call_count"])
            self.assertEqual(3, metadata["classification_evidence_count"])
            self.assertEqual(3, metadata["status_evidence_count"])
            self.assertEqual(service.state.content_sha256, metadata["content_sha256"])
            self.assertEqual("STOCK_LIBRARY_SYNC_SUCCEEDED", events[0][0])
            self.assertEqual(1, len(events))
            diagnostics = root / "runtime" / "diagnostics"
            self.assertEqual([], list(diagnostics.glob("*.json")))
            self.assertFalse(service.state.diagnostic_file_written)
            self.assertEqual("", service.state.diagnostic_file_name)
            event_details = events[0][1]["details"]
            self.assertFalse(event_details["issue_detected"])
            self.assertEqual(0, event_details["invalid_count"])
            diagnostic_writer.assert_not_called()

    def test_name_lookup_is_split_across_event_loop_batches(self) -> None:
        api = _FakeApi(
            market_codes={"0": ["005930", "000660"], "10": ["035720", "051910"]},
            names={
                "005930": "삼성전자",
                "000660": "SK하이닉스",
                "035720": "카카오",
                "051910": "LG화학",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api, minimum_count=4)
            service.start_for_current_session()
            scheduler.callbacks.pop(0)()
            self.assertEqual(["0", "10", "NXT"], api.market_calls)
            self.assertEqual([], api.name_calls)
            scheduler.callbacks.pop(0)()
            self.assertEqual(2, len(api.name_calls))
            self.assertTrue(scheduler.callbacks)
            scheduler.drain()
            self.assertEqual(4, len(api.name_calls))
            self.assertEqual(4, len(api.stock_state_calls))
            self.assertEqual(4, len(api.construction_calls))

    def test_master_status_failure_isolated_to_one_record(self) -> None:
        api = _FakeApi(
            stock_states={
                "005930": RuntimeError("state unavailable"),
                "000660": "거래정지",
                "035720": "",
            },
            constructions={
                "005930": RuntimeError("construction unavailable"),
                "000660": "투자경고",
                "035720": "",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            records = {
                item["code"]: item
                for item in json.loads(service.runtime_library_path.read_text(encoding="utf-8"))
            }
            self.assertEqual("", records["005930"]["status"])
            self.assertEqual("투자경고 | 거래정지", records["000660"]["status"])
            self.assertEqual("", records["035720"]["status"])
            self.assertEqual(1, service.state.failed_master_stock_state_count)
            self.assertEqual(1, service.state.failed_master_construction_count)

    def test_duplicate_code_is_normalized_once(self) -> None:
        api = _FakeApi(market_codes={"0": ["005930", "000660"], "10": ["005930", "035720"]})
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("SUCCEEDED", service.state.state)
            self.assertEqual(1, service.state.duplicate_count)
            self.assertEqual(1, api.name_calls.count("005930"))

    def test_unavailable_nxt_master_data_is_preserved_as_unknown(self) -> None:
        api = _FakeApi(
            market_codes={"0": ["005930", "000660"], "10": ["035720"]},
            names={"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            service.start_for_current_session()
            scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            records = json.loads(service.runtime_library_path.read_text(encoding="utf-8"))
            self.assertTrue(all(item["nxt_available"] is None for item in records))
            metadata = json.loads(service.runtime_metadata_path.read_text(encoding="utf-8"))
            self.assertEqual("UNAVAILABLE", metadata["nxt_eligibility_state"])
            self.assertEqual(0, metadata["nxt_available_count"])

    def test_alphanumeric_master_codes_are_committed_and_counted(self) -> None:
        api = _FakeApi(
            market_codes={"0": ["005930", "00088k"], "10": ["0000D0"]},
            names={"005930": "삼성전자", "00088K": "알파K", "0000D0": "알파D"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            self.assertEqual("SUCCEEDED", service.state.state)
            self.assertEqual(1, service.state.numeric_code_count)
            self.assertEqual(2, service.state.alphanumeric_code_count)
            self.assertIn("0000D0", api.stock_state_calls)
            self.assertIn("00088K", api.construction_calls)
            self.assertEqual(
                ["0000D0", "00088K", "005930"],
                [item["code"] for item in json.loads(service.runtime_library_path.read_text(encoding="utf-8"))],
            )

    def test_conflicting_duplicate_name_blocks_validation(self) -> None:
        records = [
            {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
            {"code": "005930", "name": "다른이름", "market": "KOSPI"},
        ]
        with self.assertRaises(StockLibraryValidationError) as raised:
            validate_stock_library_records(
                records,
                market_raw_counts={"KOSPI": 1, "KOSDAQ": 1},
                raw_code_count=2,
                name_lookup_count=2,
                failed_name_count=0,
                minimum_count=1,
            )
        self.assertEqual("DUPLICATE_NAME_CONFLICT", raised.exception.reason_code)

    def test_empty_market_fails_and_preserves_existing_cache(self) -> None:
        api = _FakeApi(market_codes={"0": ["005930"], "10": []})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "runtime" / "stock_library.json"
            cache.parent.mkdir(parents=True)
            cache.write_text('[{"code":"111111","name":"기존","market":"KOSPI","chosung":"ㄱㅈ"}]\n', encoding="utf-8")
            before = cache.read_bytes()
            service, scheduler, events = self._service(root, api, minimum_count=1)
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual(before, cache.read_bytes())
            self.assertEqual(["STOCK_LIBRARY_SYNC_FAILED"], [item[0] for item in events])

    def test_name_failure_threshold_allows_small_loss_and_blocks_excess(self) -> None:
        api = _FakeApi(
            market_codes={"0": ["005930", "000660", "051910"], "10": ["035720"]},
            names={
                "005930": "삼성전자",
                "000660": "SK하이닉스",
                "051910": "",
                "035720": "카카오",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(
                Path(tmp),
                api,
                minimum_count=3,
                minimum_name_success_ratio=0.75,
            )
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("SUCCEEDED", service.state.state)

        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(
                Path(tmp),
                _FakeApi(
                    market_codes={"0": ["005930", "000660", "051910"], "10": ["035720"]},
                    names={
                        "005930": "삼성전자",
                        "000660": "SK하이닉스",
                        "051910": "",
                        "035720": "카카오",
                    },
                ),
                minimum_count=3,
                minimum_name_success_ratio=0.95,
            )
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual("NAME_SUCCESS_RATIO_TOO_LOW", service.state.reason)

    def test_final_count_below_minimum_blocks_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), minimum_count=4)
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertFalse(service.runtime_library_path.exists())

    def test_excess_invalid_codes_block_commit(self) -> None:
        market_codes = {
            "0": ["005930"] + [f"BAD{index}" for index in range(5)],
            "10": ["035720"],
        }
        api = _FakeApi(
            market_codes=market_codes,
            names={"005930": "삼성전자", "035720": "카카오"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api, minimum_count=2)
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual("VALID_CODE_RATIO_TOO_LOW", service.state.reason)

    def test_invalid_diagnostic_captures_raw_tokens_reasons_and_master_names(self) -> None:
        api = self._incident_api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root, api, minimum_count=2)
            with patch.object(
                service,
                "_write_diagnostic_file",
                wraps=service._write_diagnostic_file,
            ) as diagnostic_writer:
                service.start_for_current_session()
                scheduler.drain()

            self.assertEqual("FAILED", service.state.state)
            self.assertEqual("VALID_CODE_RATIO_TOO_LOW", service.state.reason)
            self.assertTrue(service.state.diagnostic_file_written)
            diagnostic_path = root / "runtime" / "diagnostics" / service.state.diagnostic_file_name
            payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            summary = payload["summary"]
            self.assertEqual(5, summary["invalid_count"])
            self.assertEqual(
                {
                    "CONTROL_CHARACTER": 1,
                    "LENGTH_NOT_6": 2,
                    "SPECIAL_CHARACTER": 1,
                    "ZERO_CODE": 1,
                },
                summary["invalid_by_reason"],
            )
            self.assertEqual(summary["invalid_count"], sum(summary["invalid_by_reason"].values()))
            self.assertEqual(1, summary["invalid_master_name_found"])
            self.assertEqual(4, summary["invalid_master_name_missing"])
            self.assertEqual(1, api.name_calls.count("BAD01"))
            self.assertEqual(1, api.name_calls.count("ABC123"))
            self.assertEqual(1, api.name_calls.count("000000"))
            self.assertEqual(1, api.name_calls.count("1234567"))
            self.assertEqual(1, api.name_calls.count("12-345"))
            self.assertEqual(1, api.name_calls.count("\x0012345"))
            self.assertNotIn("markets", payload)
            whitespace = next(
                item for item in payload["invalid_items"] if item["raw_token"] == " BAD01"
            )
            self.assertTrue(whitespace["leading_whitespace"])
            self.assertEqual("' BAD01'", whitespace["raw_repr"])
            control_item = next(
                item for item in payload["invalid_items"] if item["invalid_reason"] == "CONTROL_CHARACTER"
            )
            self.assertTrue(control_item["has_control_character"])
            event_details = events[-1][1]["details"]
            self.assertEqual(summary["invalid_by_reason"], event_details["invalid_by_reason"])
            self.assertTrue(event_details["issue_detected"])
            self.assertEqual(5, event_details["invalid_count"])
            self.assertTrue(event_details["diagnostic_file_written"])
            self.assertEqual(diagnostic_path.name, event_details["diagnostic_file_name"])
            self.assertNotIn("invalid_items", event_details)
            self.assertFalse(diagnostic_path.with_name(diagnostic_path.name + ".tmp").exists())
            self.assertFalse(service.runtime_library_path.exists())
            diagnostic_writer.assert_called_once()

            legacy_payload = dict(payload)
            legacy_payload["markets"] = service._market_diagnostics
            legacy_size = len(service._json_bytes(legacy_payload))
            self.assertLess(diagnostic_path.stat().st_size, legacy_size)

    def test_diagnostic_write_failure_is_fail_open_for_existing_sync_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(
                root,
                self._incident_api(),
                minimum_count=2,
            )
            with patch.object(
                service,
                "_write_diagnostic_file",
                side_effect=OSError("diagnostic unavailable"),
            ):
                service.start_for_current_session()
                scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual("VALID_CODE_RATIO_TOO_LOW", service.state.reason)
            self.assertFalse(service.state.diagnostic_file_written)
            self.assertFalse(events[-1][1]["details"]["diagnostic_file_written"])
            self.assertEqual("", events[-1][1]["details"]["diagnostic_file_name"])

    def test_incident_snapshot_replaces_same_session_without_file_growth(self) -> None:
        api = self._incident_api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root, api, minimum_count=2)

            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            diagnostics = root / "runtime" / "diagnostics"
            paths = list(diagnostics.glob("*.json"))
            self.assertEqual(1, len(paths))
            first_path = paths[0]
            first_payload = json.loads(first_path.read_text(encoding="utf-8"))

            api.raw_market_returns["10"] = "035720;1234567;12-345;ABC123;\x0012345;"
            self.assertTrue(service.start_for_current_session(explicit_retry=True))
            scheduler.drain()

            paths = list(diagnostics.glob("*.json"))
            self.assertEqual([first_path], paths)
            second_payload = json.loads(first_path.read_text(encoding="utf-8"))
            self.assertNotEqual(first_payload["summary"], second_payload["summary"])
            self.assertEqual(2, len(events))
            self.assertTrue(all(event[1]["details"]["issue_detected"] for event in events))

    def test_incidents_from_different_sessions_keep_distinct_snapshots(self) -> None:
        api = self._incident_api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, _events = self._service(root, api, minimum_count=2)

            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            api.session_id = "SESSION-2"
            api.epoch = 2
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()

            paths = sorted((root / "runtime" / "diagnostics").glob("*.json"))
            self.assertEqual(2, len(paths))
            identities = {
                (
                    json.loads(path.read_text(encoding="utf-8"))["connection_epoch"],
                    json.loads(path.read_text(encoding="utf-8"))["login_session_id"],
                )
                for path in paths
            }
            self.assertEqual({(1, "SESSION-1"), (2, "SESSION-2")}, identities)

    def test_normal_retry_preserves_prior_same_session_incident_snapshot(self) -> None:
        api = self._incident_api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root, api, minimum_count=2)

            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            diagnostic_path = next((root / "runtime" / "diagnostics").glob("*.json"))
            incident_bytes = diagnostic_path.read_bytes()

            api.raw_market_returns = {}
            api.market_codes = {
                "0": ["005930", "000660"],
                "10": ["035720"],
                "NXT": ["005930"],
            }
            api.names.update({"000660": "SK하이닉스"})
            self.assertTrue(service.start_for_current_session(explicit_retry=True))
            scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            self.assertEqual(incident_bytes, diagnostic_path.read_bytes())
            self.assertEqual(1, len(list(diagnostic_path.parent.glob("*.json"))))
            self.assertFalse(events[-1][1]["details"]["issue_detected"])
            self.assertFalse(events[-1][1]["details"]["diagnostic_file_written"])

    def test_repeated_normal_sync_never_creates_diagnostic_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root)
            for attempt in range(100):
                self.assertTrue(
                    service.start_for_current_session(explicit_retry=attempt > 0)
                )
                scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            self.assertEqual([], list((root / "runtime" / "diagnostics").glob("*.json")))
            self.assertEqual(100, len(events))
            self.assertTrue(
                all(not event[1]["details"]["issue_detected"] for event in events)
            )

    def test_three_invalid_codes_create_compact_incident_snapshot(self) -> None:
        api = _FakeApi(
            raw_market_returns={
                "0": "005930;BAD01;000000;",
                "10": "035720;12-345;",
            },
            names={"005930": "삼성전자", "035720": "카카오"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, events = self._service(root, api, minimum_count=2)
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()

            diagnostic_path = next((root / "runtime" / "diagnostics").glob("*.json"))
            payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(3, payload["summary"]["invalid_count"])
            self.assertEqual(3, len(payload["invalid_items"]))
            self.assertNotIn("markets", payload)
            self.assertTrue(events[-1][1]["details"]["issue_detected"])

    def test_atomic_promote_failure_preserves_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "runtime" / "stock_library.json"
            cache.parent.mkdir(parents=True)
            cache.write_text('[{"code":"111111","name":"기존","market":"KOSPI","chosung":"ㄱㅈ"}]\n', encoding="utf-8")
            before = cache.read_bytes()
            service, scheduler, _events = self._service(root)
            with patch.object(service, "_promote_temp", side_effect=OSError("promote failed")):
                service.start_for_current_session()
                scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual(before, cache.read_bytes())

    def test_metadata_temp_write_failure_removes_library_temp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, _events = self._service(root)
            original = service._write_temp
            def fail_ready_metadata(path, payload):
                if (
                    Path(path) == service.runtime_metadata_path
                    and b'"sync_state": "READY"' in payload
                ):
                    raise OSError("metadata write failed")
                return original(path, payload)

            with patch.object(service, "_write_temp", side_effect=fail_ready_metadata):
                service.start_for_current_session()
                scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertFalse((root / "runtime" / "stock_library.json.tmp").exists())
            self.assertFalse(service.runtime_library_path.exists())

    def test_staged_readback_mismatch_preserves_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "runtime" / "stock_library.json"
            cache.parent.mkdir(parents=True)
            cache.write_text('[{"code":"111111","name":"기존","market":"KOSPI","chosung":"ㄱㅈ"}]\n', encoding="utf-8")
            before = cache.read_bytes()
            service, scheduler, _events = self._service(root)
            with patch.object(service, "_read_json", return_value=[]):
                service.start_for_current_session()
                scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual(before, cache.read_bytes())

    def test_same_session_triggers_once_and_new_session_can_sync(self) -> None:
        api = _FakeApi()
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            self.assertTrue(service.start_for_current_session())
            self.assertFalse(service.start_for_current_session())
            scheduler.drain()
            self.assertFalse(service.start_for_current_session())
            self.assertEqual(["0", "10", "NXT"], api.market_calls)

            api.session_id = "SESSION-2"
            api.epoch = 2
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            self.assertEqual(
                ["0", "10", "NXT", "0", "10", "NXT"],
                api.market_calls,
            )

    def test_only_verified_runtime_library_is_used_without_root_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = [{"code": "005930", "name": "번들", "market": "KOSPI", "chosung": "ㅂㄷ"}]
            runtime = [{"code": "000660", "name": "런타임", "market": "KOSPI", "chosung": "ㄹㅌㅇ"}]
            (root / "stock_library.json").write_text(json.dumps(bundled, ensure_ascii=False), encoding="utf-8")
            (root / "runtime").mkdir()
            runtime_path = root / "runtime" / "stock_library.json"
            runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
            runtime_bytes = runtime_path.read_bytes()
            (root / "runtime" / "stock_library_meta.json").write_text(
                json.dumps(
                    {
                        "sync_state": "READY",
                        "final_count": 1,
                        "content_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual("000660", load_stock_library(root)[0]["code"])

            runtime_with_status = [
                {
                    **runtime[0],
                    "status": "투자경고 | 거래정지",
                    "master_stock_state": "거래정지",
                    "master_construction": "투자경고",
                    "master_stock_info": "시장구분0|코스피;시장구분1|중형주;",
                    "classification": "일반종목",
                }
            ]
            runtime_path.write_text(json.dumps(runtime_with_status, ensure_ascii=False), encoding="utf-8")
            runtime_bytes = runtime_path.read_bytes()
            (root / "runtime" / "stock_library_meta.json").write_text(
                json.dumps(
                    {
                        "sync_state": "READY",
                        "final_count": 1,
                        "content_sha256": hashlib.sha256(runtime_bytes).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_stock_library(root)[0]
            self.assertEqual("투자경고 | 거래정지", loaded["status"])
            self.assertEqual("거래정지", loaded["master_stock_state"])
            self.assertEqual("투자경고", loaded["master_construction"])
            self.assertEqual("시장구분0|코스피;시장구분1|중형주;", loaded["master_stock_info"])
            self.assertEqual("일반종목", loaded["classification"])

            runtime_path.write_text("{broken", encoding="utf-8")
            self.assertEqual([], load_stock_library(root))

            (root / "runtime" / "stock_library_meta.json").write_text(
                json.dumps({"sync_state": "FAILED", "reason_code": "TEST_FAILURE"}),
                encoding="utf-8",
            )
            self.assertEqual([], load_stock_library(root))

            (root / "runtime" / "stock_library_meta.json").write_text(
                json.dumps({"sync_state": "SYNCING"}),
                encoding="utf-8",
            )
            snapshot = load_stock_library_snapshot(root)
            self.assertEqual(STOCK_LIBRARY_SYNCING, snapshot.state)
            self.assertEqual([], list(snapshot.records))

            runtime_path.write_text(
                json.dumps(runtime + runtime, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual([], load_stock_library(root))

    def test_search_uses_cached_library_and_only_requests_batched_snapshot(self) -> None:
        api = MagicMock()
        api.request_initial_market_snapshot.return_value = {
            "ok": True,
            "status": "ENQUEUED",
            "batch_count": 1,
        }
        library = [{"code": "005930", "name": "삼성전자", "market": "KOSPI", "chosung": "ㅅㅅㅈㅈ"}]
        snapshot = StockLibraryLoadSnapshot(
            STOCK_LIBRARY_READY,
            STOCK_LIBRARY_RUNTIME_SOURCE,
            tuple(library),
        )
        with (
            patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot),
            patch.object(setting_window, "read_base_stocks", return_value=[]),
        ):
            dialog = setting_window.InstanceStockSearchRegisterDialog(
                None,
                stock_source="server",
                kiwoom_api=api,
            )
            self.addCleanup(dialog.close)
            dialog.search_input.setText("삼성")
            dialog.search_stocks()
        api.request_initial_market_snapshot.assert_called_once()
        self.assertEqual(
            ("005930",),
            api.request_initial_market_snapshot.call_args.args[0],
        )
        api.get_market_stock_codes.assert_not_called()
        api.get_master_stock_name.assert_not_called()
        api.get_master_stock_state.assert_not_called()
        api.get_master_construction.assert_not_called()
        api.get_master_stock_info.assert_not_called()
        self.assertEqual(STOCK_LIBRARY_RUNTIME_SOURCE, dialog.stock_source)
        self.assertEqual(1, dialog.result_table.rowCount())

    def test_synced_master_status_reaches_registration_status_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, scheduler, _events = self._service(root)
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()
            snapshot = load_stock_library_snapshot(root)
            self.assertEqual(STOCK_LIBRARY_READY, snapshot.state)

            api = MagicMock()
            api.request_initial_market_snapshot.return_value = {
                "ok": True,
                "status": "ENQUEUED",
                "batch_count": 1,
            }
            with (
                patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot),
                patch.object(setting_window, "read_base_stocks", return_value=[]),
            ):
                dialog = setting_window.InstanceStockSearchRegisterDialog(
                    None,
                    stock_source="server",
                    kiwoom_api=api,
                )
                self.addCleanup(dialog.close)
                dialog.search_input.setText("카카오")
                dialog.search_stocks()

            row = dialog._find_result_row_by_stock_code("035720")
            status_item = dialog.result_table.item(row, dialog.STOCK_STATUS_COLUMN)
            self.assertEqual("투자경고 | 관리종목 | 거래정지", status_item.toolTip())
            self.assertTrue(status_item.text().endswith("..."))
            api.get_master_stock_state.assert_not_called()
            api.get_master_construction.assert_not_called()
            api.get_master_stock_info.assert_not_called()

    def test_master_stock_info_parser_is_evidence_only_and_fail_closed(self) -> None:
        cases = (
            (
                "시장구분0|코스피;시장구분1|중형주;업종구분|금융업;",
                "일반종목",
            ),
            (
                "시장구분0|코스닥|중견기업;시장구분1|소형주;업종구분|제조;",
                "일반종목",
            ),
            (
                "시장구분0|코스피;시장구분1|;업종구분|전기/전자;",
                "일반종목",
            ),
            ("시장구분0|코스피;시장구분1|ETF;업종구분|상장지수펀드;", "ETF"),
            ("시장구분0|코스피;시장구분1|ETN;업종구분|상장지수증권;", "ETN"),
            ("시장구분0|코스닥;시장구분1|스팩;업종구분|기업인수목적회사;", "SPAC"),
            ("시장구분0|코스닥|스 팩;시장구분1|소형주;업종구분|금융|", "SPAC"),
            ("시장구분0|코스피;시장구분1|리츠;업종구분|부동산투자회사;", "REIT"),
            ("시장구분0|인프라투자금융;", "기타"),
            ("시장구분0|코스피;시장구분1|ELW;업종구분|파생상품;", "기타"),
            ("", "-"),
            ("시장구분0|코스피;", "-"),
            ("시장구분0|코스피;시장구분1|;업종구분|;", "-"),
            ("시장구분0|코스닥|;업종구분||", "-"),
            ("broken-without-delimiter", "-"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(expected, classify_master_stock_info(raw))
        self.assertEqual(
            "ETN",
            classify_master_stock_info(
                "시장구분0|;",
                market_kind="60",
            ),
        )
        self.assertEqual(
            "ETF",
            classify_master_stock_info(
                "시장구분0|ETF;",
                market_kind="60",
            ),
        )
        self.assertEqual(
            (("시장구분0", "코스피"), ("시장구분1", "중형주")),
            master_stock_info_fields("시장구분0|코스피;시장구분1|중형주;"),
        )

    def test_master_stock_info_failure_isolated_to_one_record(self) -> None:
        api = _FakeApi(
            stock_infos={
                "005930": "시장구분0|코스피;시장구분1|중형주;",
                "000660": RuntimeError("master info unavailable"),
                "035720": "시장구분0|코스닥;시장구분1|우량기업;",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(Path(tmp), api)
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            records = {
                item["code"]: item
                for item in json.loads(
                    service.runtime_library_path.read_text(encoding="utf-8")
                )
            }
            self.assertEqual("-", records["000660"]["classification"])
            self.assertEqual("", records["000660"]["master_stock_info"])
            self.assertEqual(1, service.state.failed_master_stock_info_count)
            self.assertEqual(3, service.state.master_stock_info_call_count)

    def test_etn_market_kind_is_preserved_as_classification_evidence(self) -> None:
        etn_codes = [
            "520100",
            "500023",
            "500024",
            "500029",
            "500030",
            "500035",
            "500036",
            "500037",
            "500038",
        ]
        api = _FakeApi(
            market_codes={
                "0": [*etn_codes, "0162Z0"],
                "10": ["035720"],
                "NXT": [],
            },
            names={
                **{code: f"표본 {index}" for index, code in enumerate(etn_codes)},
                "0162Z0": "ETF 표본",
                "035720": "카카오",
            },
            stock_infos={
                **{code: "시장구분0|;" for code in etn_codes},
                "0162Z0": "시장구분0|ETF;",
                "035720": "시장구분0|코스닥;시장구분1|우량기업;업종구분|서비스업;",
            },
            stock_market_kinds={code: "60" for code in etn_codes},
        )
        with tempfile.TemporaryDirectory() as tmp:
            service, scheduler, _events = self._service(
                Path(tmp),
                api,
                minimum_count=len(etn_codes) + 2,
            )
            self.assertTrue(service.start_for_current_session())
            scheduler.drain()

            self.assertEqual("SUCCEEDED", service.state.state)
            records = {
                item["code"]: item
                for item in json.loads(
                    service.runtime_library_path.read_text(encoding="utf-8")
                )
            }
            for code in etn_codes:
                with self.subTest(code=code):
                    self.assertEqual("ETN", records[code]["classification"])
                    self.assertEqual("60", records[code]["master_stock_market_kind"])
            self.assertEqual("ETF", records["0162Z0"]["classification"])
            self.assertEqual("", records["0162Z0"]["master_stock_market_kind"])
            self.assertEqual("일반종목", records["035720"]["classification"])
            metadata = json.loads(
                service.runtime_metadata_path.read_text(encoding="utf-8")
            )
            self.assertEqual(len(etn_codes), metadata["etn_market_member_count"])
            self.assertEqual(len(etn_codes), metadata["master_stock_market_kind_call_count"])
            self.assertEqual(0, metadata["failed_master_stock_market_kind_count"])
            self.assertEqual(["0", "10", "NXT"], api.market_calls)
            self.assertEqual(etn_codes, api.stock_market_kind_calls)
            loaded = {
                item["code"]: item
                for item in load_stock_library(Path(tmp))
            }
            self.assertEqual("ETN", loaded["520100"]["classification"])
            self.assertEqual("60", loaded["520100"]["master_stock_market_kind"])

    def test_sync_failure_never_changes_registered_stocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stocks = root / "stocks" / "005930_삼성전자" / "config.json"
            stocks.parent.mkdir(parents=True)
            stocks.write_text('{"code":"005930"}\n', encoding="utf-8")
            before = stocks.read_bytes()
            service, scheduler, _events = self._service(
                root,
                _FakeApi(market_codes={"0": [], "10": []}),
                minimum_count=1,
            )
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("FAILED", service.state.state)
            self.assertEqual(before, stocks.read_bytes())

    def test_chosung_preserves_latin_and_digits(self) -> None:
        self.assertEqual("SKㅎㅇㄴㅅ2", stock_name_chosung("SK하이닉스2"))

    def test_sync_event_contracts_are_system_events(self) -> None:
        self.assertEqual("SYSTEM", EVENT_TYPE_CATEGORIES["STOCK_LIBRARY_SYNC_SUCCEEDED"])
        self.assertEqual("SYSTEM", EVENT_TYPE_CATEGORIES["STOCK_LIBRARY_SYNC_FAILED"])
        self.assertIn("동기화", SUMMARY_TEMPLATES["STOCK_LIBRARY_SYNC_SUCCEEDED"])

    def test_event_writer_failure_does_not_change_successful_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = _Scheduler()
            service = KiwoomStockLibrarySyncService(
                _FakeApi(),
                project_root=Path(tmp),
                batch_size=2,
                minimum_count=3,
                event_writer=MagicMock(side_effect=RuntimeError("journal unavailable")),
                scheduler=scheduler,
            )
            service.start_for_current_session()
            scheduler.drain()
            self.assertEqual("SUCCEEDED", service.state.state)
            self.assertTrue(service.runtime_library_path.exists())


class KiwoomMasterWrapperTests(unittest.TestCase):
    def _api(self, control):
        api = KiwoomApi.__new__(KiwoomApi)
        api._control = control
        api.broker_readiness_snapshot = lambda: SimpleNamespace(
            broker_request_ready=True,
            reason="READY",
        )
        return api

    def test_wrappers_normalize_codes_and_names_without_tr_calls(self) -> None:
        control = MagicMock()
        control.dynamicCall.side_effect = [
            "005930; 000660;005930;;",
            " 삼성전자 ",
            "정상|거래정지|관리종목|",
            "투자경고",
            "시장구분0|코스피;시장구분1|중형주;업종구분|금융업;",
        ]
        api = self._api(control)
        codes = api.get_market_stock_codes("0")
        name = api.get_master_stock_name("005930")
        state = api.get_master_stock_state("005930")
        construction = api.get_master_construction("005930")
        stock_info = api.get_master_stock_info("005930")
        self.assertEqual(["005930", "000660"], codes["value"])
        diagnostic = codes["diagnostic"]
        self.assertEqual(5, diagnostic["split_token_count"])
        self.assertEqual(2, diagnostic["normalized_unique_count"])
        self.assertEqual(" 000660", diagnostic["tokens"][1]["raw_token"])
        self.assertTrue(diagnostic["tokens"][1]["leading_whitespace"])
        self.assertTrue(any(item["invalid_reason"] == "EMPTY" for item in diagnostic["tokens"]))
        self.assertEqual("삼성전자", name["value"])
        self.assertEqual("정상|거래정지|관리종목|", state["value"])
        self.assertEqual("투자경고", construction["value"])
        self.assertEqual("일반종목", classify_master_stock_info(stock_info["value"]))
        signatures = [call.args[0] for call in control.dynamicCall.call_args_list]
        self.assertEqual(
            [
                "GetCodeListByMarket(QString)",
                "GetMasterCodeName(QString)",
                "GetMasterStockState(QString)",
                "GetMasterConstruction(QString)",
                "KOA_Functions(QString, QString)",
            ],
            signatures,
        )
        self.assertNotIn("CommRqData", " ".join(signatures))
        self.assertNotIn("SetInputValue", " ".join(signatures))
        self.assertEqual(
            (
                "KOA_Functions(QString, QString)",
                "GetMasterStockInfo",
                "005930",
            ),
            control.dynamicCall.call_args_list[-1].args,
        )

    def test_wrapper_fails_closed_when_broker_is_not_ready(self) -> None:
        control = MagicMock()
        api = KiwoomApi.__new__(KiwoomApi)
        api._control = control
        api.broker_readiness_snapshot = lambda: SimpleNamespace(
            broker_request_ready=False,
            reason="DISCONNECTED",
        )
        self.assertFalse(api.get_market_stock_codes("0")["ok"])
        self.assertFalse(api.get_master_stock_name("005930")["ok"])
        self.assertFalse(api.get_master_stock_state("005930")["ok"])
        self.assertFalse(api.get_master_construction("005930")["ok"])
        self.assertFalse(api.get_master_stock_info("005930")["ok"])
        control.dynamicCall.assert_not_called()

    def test_master_status_wrappers_keep_alphanumeric_identity_and_allow_empty(self) -> None:
        control = MagicMock()
        control.dynamicCall.side_effect = ["", "정상"]
        api = self._api(control)

        state = api.get_master_stock_state("0134x0")
        construction = api.get_master_construction("0164h0")

        self.assertTrue(state["ok"])
        self.assertEqual("EMPTY", state["reason"])
        self.assertEqual("", state["value"])
        self.assertTrue(construction["ok"])
        self.assertEqual("정상", construction["value"])
        self.assertEqual(
            [
                ("GetMasterStockState(QString)", "0134X0"),
                ("GetMasterConstruction(QString)", "0164H0"),
            ],
            [(call.args[0], call.args[1]) for call in control.dynamicCall.call_args_list],
        )

    def test_nxt_market_uses_master_function_without_tr(self) -> None:
        control = MagicMock()
        control.dynamicCall.return_value = "005930;035720;"
        api = self._api(control)

        result = api.get_market_stock_codes("NXT")

        self.assertTrue(result["ok"])
        self.assertEqual(["005930", "035720"], result["value"])
        control.dynamicCall.assert_called_once_with(
            "GetCodeListByMarket(QString)",
            "NXT",
        )
        self.assertNotIn("CommRqData", control.dynamicCall.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
