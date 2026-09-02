from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_trace_contract import (
    SCHEMA_VERSION,
    build_trace_record,
    live_trace_mode_expired,
    new_trace_id,
    new_trace_record_id,
    resolve_live_trace_mode,
    validate_trace_record,
)
from decision_trace_reader import DecisionTraceReader
from decision_trace_snapshot_service import (
    DecisionTraceSnapshotService,
    compute_engine_bundle_identity,
    content_hash,
    normalize_rules,
    normalize_settings,
)
from decision_trace_writer import DecisionTraceWriter
from market_evidence_store import MarketEvidenceStore, market_window_hash, normalize_candle_window


TRACE_ID = "11111111-1111-4111-8111-111111111111"
RECORD_ID = "22222222-2222-4222-8222-222222222222"
HASH_A = "a" * 64
HASH_B = "b" * 64


class DecisionTraceFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.live_dir = self.root / "live"
        self.writer = DecisionTraceWriter(self.live_dir, backtest_trace_dir=self.root / "backtests")
        self.reader = DecisionTraceReader(self.live_dir)

    def tearDown(self) -> None:
        self._temp.cleanup()

    @staticmethod
    def _dataset() -> dict[str, object]:
        return {
            "dataset_id": "dataset-fixture",
            "source": "FIXTURE",
            "data_hash": HASH_A,
            "timeframe": "1m",
            "timezone": "Asia/Seoul",
            "bar_time": "2026-08-08T09:00:00+09:00",
            "bar_index": 12,
            "bar_count": 30,
            "input_window_hash": HASH_B,
        }

    @staticmethod
    def _rule_ref() -> dict[str, object]:
        return {
            "routine_definition_id": "indicator_follow",
            "routine_instance_id": "routine-1",
            "routine_type": "INDICATOR_FOLLOW",
            "rules_version": "0.2.0",
            "rules_hash": HASH_A,
            "settings_hash": HASH_B,
            "engine_bundle_hash": "c" * 64,
        }

    @staticmethod
    def _condition(*, null_operand: bool = False) -> dict[str, object]:
        right = {
            "source": "RULE",
            "key": "threshold",
            "index": None,
            "value": None if null_operand else 30,
        }
        if null_operand:
            right["reason"] = "threshold unavailable"
        return {
            "condition_ref": {"rules_hash": HASH_A, "path": "buy.groups[0].conditions[0]"},
            "condition_type": "RSI",
            "operator": "<=",
            "negated": False,
            "left_operand": {"source": "INDICATOR", "key": "RSI:14", "index": 12, "value": 28.4},
            "right_operand": right,
            "raw_result": True,
            "final_result": True,
        }

    @staticmethod
    def _group() -> dict[str, object]:
        return {
            "group_ref": {"rules_hash": HASH_A, "path": "buy.groups[0]"},
            "group_name": "매수조건1",
            "enabled": True,
            "logic": "AND",
            "condition_refs": [{"rules_hash": HASH_A, "path": "buy.groups[0].conditions[0]"}],
            "result": True,
        }

    def _decision(
        self,
        *,
        trace_id: str = TRACE_ID,
        record_id: str = RECORD_ID,
        final_decision: str = "BUY",
        evaluation_status: str = "COMPLETED",
        trace_level: str = "NORMAL",
        recorded_at: str = "2026-08-08T09:00:00+09:00",
        **extra: object,
    ) -> dict[str, object]:
        return build_trace_record(
            trace_id=trace_id,
            trace_record_id=record_id,
            recorded_at=recorded_at,
            environment="LIVE",
            trace_level=trace_level,
            stage="DECISION",
            stage_result=evaluation_status,
            decision_at=recorded_at,
            dataset_ref=self._dataset(),
            rule_ref=self._rule_ref(),
            conditions=[self._condition()],
            groups=[self._group()],
            final_decision=final_decision,
            evaluation_status=evaluation_status,
            stock_code="005930",
            routine_instance_id="routine-1",
            **extra,
        )

    def _stage_record(
        self,
        stage: str,
        *,
        record_id: str = "33333333-3333-4333-8333-333333333333",
        result: str = "BLOCKED",
        trace_id: str = TRACE_ID,
        recorded_at: str = "2026-08-08T09:00:01+09:00",
    ) -> dict[str, object]:
        return build_trace_record(
            trace_id=trace_id,
            trace_record_id=record_id,
            recorded_at=recorded_at,
            environment="LIVE",
            trace_level="NORMAL",
            stage=stage,
            stage_result=result,
            stock_code="005930",
            routine_instance_id="routine-1",
        )

    def test_contract_accepts_normal_decision_and_uuid4_identities(self) -> None:
        record = self._decision(signal_id="signal-1", order_id="order-1", event_journal_event_id="event-1")
        self.assertEqual([], validate_trace_record(record))
        self.assertEqual(4, __import__("uuid").UUID(new_trace_id()).version)
        self.assertEqual(4, __import__("uuid").UUID(new_trace_record_id()).version)

    def test_contract_rejects_invalid_common_enums(self) -> None:
        for field, value in (
            ("environment", "PAPER"),
            ("trace_level", "FULL"),
            ("stage", "QUEUE"),
            ("stage_result", "READY"),
        ):
            record = self._decision()
            record[field] = value
            self.assertTrue(validate_trace_record(record), field)

    def test_contract_requires_decision_fields_and_valid_values(self) -> None:
        for field in ("decision_at", "dataset_ref", "rule_ref", "conditions", "groups", "final_decision", "evaluation_status"):
            record = self._decision()
            record.pop(field)
            self.assertTrue(validate_trace_record(record), field)
        for decision in ("BUY", "SELL"):
            self.assertEqual([], validate_trace_record(self._decision(final_decision=decision)))
        diagnostic_none = self._decision(final_decision="NONE", trace_level="DIAGNOSTIC")
        self.assertEqual([], validate_trace_record(diagnostic_none))
        invalid = self._decision(evaluation_status="READY")
        self.assertTrue(validate_trace_record(invalid))

    def test_condition_group_and_null_operand_contract(self) -> None:
        record = self._decision(trace_level="DIAGNOSTIC")
        record["conditions"] = [self._condition(null_operand=True)]
        self.assertEqual([], validate_trace_record(record))
        del record["conditions"][0]["right_operand"]["reason"]
        self.assertTrue(validate_trace_record(record))
        record = self._decision(trace_level="DIAGNOSTIC")
        del record["groups"][0]["group_ref"]["path"]
        self.assertTrue(validate_trace_record(record))

    def test_indicator_aggregation_position_and_cycle_contracts_are_bounded(self) -> None:
        record = self._decision(
            trace_level="DIAGNOSTIC",
            indicator_snapshots=[{"indicator": "RSI", "period": 14, "index": 12, "current": 28.4, "previous": 31.2}],
            position_context={"holding_qty": 3, "average_price": 70000, "position_state": "OPEN"},
            cycle_context={"cycle_identity": "cycle-1", "cycle_active": True, "confirmed_buy_round": 1},
            buy_aggregation={"logic": "OR", "group_refs": [{"rules_hash": HASH_A, "path": "buy.groups[0]"}], "result": True},
        )
        self.assertEqual([], validate_trace_record(record))
        record["position_context"]["runtime"] = {"orders": []}
        self.assertTrue(validate_trace_record(record))

    def test_backtest_contract_requires_run_id_and_level(self) -> None:
        record = self._decision(trace_level="BACKTEST")
        record["environment"] = "BACKTEST"
        self.assertTrue(validate_trace_record(record))
        record["backtest_run_id"] = "run-1"
        self.assertEqual([], validate_trace_record(record))

    def test_normal_and_diagnostic_record_policy(self) -> None:
        self.assertTrue(validate_trace_record(self._decision(final_decision="NONE")))
        self.assertEqual([], validate_trace_record(self._decision(final_decision="NONE", trace_level="DIAGNOSTIC")))
        self.assertEqual([], validate_trace_record(self._stage_record("POLICY")))
        self.assertTrue(validate_trace_record(self._stage_record("POLICY", result="PASSED")))
        self.assertEqual([], validate_trace_record(self._stage_record("EXCEPTION", result="ERROR")))

    def test_rules_hash_is_stable_and_excludes_ui_metadata(self) -> None:
        first = {
            "buy": {"enabled": True, "description": "UI text", "groups": [{"name": "A", "conditions": []}]},
            "indicator_follow_ui_state": {"state": {"x": 1}},
            "updated_at": "yesterday",
        }
        second = {
            "updated_at": "today",
            "indicator_follow_ui_state": {"state": {"x": 999}},
            "buy": {"groups": [{"conditions": [], "name": "A"}], "description": "changed", "enabled": True},
        }
        excluded = ("indicator_follow_ui_state",)
        self.assertEqual(
            content_hash(normalize_rules(first, excluded_keys=excluded)),
            content_hash(normalize_rules(second, excluded_keys=excluded)),
        )
        changed = {"buy": {"enabled": False, "groups": [{"name": "A", "conditions": []}]}}
        self.assertNotEqual(
            content_hash(normalize_rules(first, excluded_keys=excluded)),
            content_hash(normalize_rules(changed, excluded_keys=excluded)),
        )

    def test_settings_hash_excludes_runtime_context(self) -> None:
        first = {
            "trade_amount_type": "AMOUNT",
            "buy_amount": 100000.0,
            "buy_limit_enabled": True,
            "buy_limit_amount": 500000,
            "current_price": 70000,
            "holding_qty": 3,
            "cycle": {"active": True},
        }
        second = dict(first, current_price=80000, holding_qty=8, cycle={"active": False})
        self.assertEqual(normalize_settings(first), normalize_settings(second))
        self.assertEqual(content_hash(normalize_settings(first)), content_hash(normalize_settings(second)))

    def test_snapshot_is_content_addressed_duplicate_safe_and_immutable(self) -> None:
        service = DecisionTraceSnapshotService(self.root / "evidence", now_factory=lambda: "2026-08-08T09:00:00+09:00")
        saved = service.save_rules({"buy": {"enabled": True}, "description": "x"})
        duplicate = service.save_rules({"description": "y", "buy": {"enabled": True}})
        self.assertTrue(saved["saved"])
        self.assertTrue(duplicate["duplicate"])
        path = Path(saved["path"])
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["hash"], document["hash"])
        self.assertEqual(saved["hash"], content_hash(document["payload"]))
        document["payload"] = {"tampered": True}
        path.write_text(json.dumps(document), encoding="utf-8")
        collision = service.save_rules({"buy": {"enabled": True}})
        self.assertTrue(collision["integrity_error"])

    def test_settings_snapshot_duplicate_does_not_overwrite(self) -> None:
        service = DecisionTraceSnapshotService(self.root / "evidence", now_factory=lambda: "2026-08-08T09:00:00+09:00")
        saved = service.save_settings({"buy_qty": 1, "current_price": 100})
        before = Path(saved["path"]).read_bytes()
        duplicate = service.save_settings({"current_price": 999, "buy_qty": 1})
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(before, Path(saved["path"]).read_bytes())

    @staticmethod
    def _candles() -> list[dict[str, object]]:
        return [
            {"timestamp": "2026-08-08T09:00:00+09:00", "open": "100", "high": 110, "low": 90, "close": 105.0, "volume": 1000},
            {"timestamp": "2026-08-08T09:01:00+09:00", "open": 105, "high": 112, "low": 101, "close": 108, "volume": 1200},
        ]

    def test_market_evidence_hash_value_and_order_sensitivity(self) -> None:
        candles = self._candles()
        self.assertEqual(market_window_hash(candles), market_window_hash(json.loads(json.dumps(candles))))
        changed = json.loads(json.dumps(candles))
        changed[1]["close"] = 109
        self.assertNotEqual(market_window_hash(candles), market_window_hash(changed))
        self.assertNotEqual(market_window_hash(candles), market_window_hash(list(reversed(candles))))
        self.assertEqual("2026-08-08T09:00:00+09:00", normalize_candle_window(candles)[0]["timestamp"])

    def test_market_evidence_duplicate_integrity_and_sensitive_rejection(self) -> None:
        store = MarketEvidenceStore(self.root / "market", now_factory=lambda: "2026-08-08T09:00:00+09:00")
        saved = store.save_window(self._candles())
        duplicate = store.save_window(self._candles())
        self.assertTrue(saved["saved"])
        self.assertTrue(duplicate["duplicate"])
        path = Path(saved["path"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document["payload"][0]["close"] = 999
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(store.save_window(self._candles())["integrity_error"])
        sensitive = self._candles()
        sensitive[0]["account_no"] = "1234567890"
        self.assertTrue(store.save_window(sensitive)["invalid"])

    def test_engine_bundle_identity_is_path_independent_and_source_sensitive(self) -> None:
        roots = [self.root / "one", self.root / "two"]
        for root in roots:
            (root / "engine").mkdir(parents=True)
            (root / "engine" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "engine" / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
        first = compute_engine_bundle_identity(("engine/a.py", "engine/b.py"), project_root=roots[0])
        second = compute_engine_bundle_identity(("engine/a.py", "engine/b.py"), project_root=roots[1])
        self.assertEqual(first["engine_bundle_hash"], second["engine_bundle_hash"])
        absolute = compute_engine_bundle_identity(
            (roots[0] / "engine" / "a.py", roots[0] / "engine" / "b.py"),
            project_root=roots[0],
        )
        self.assertEqual(first["engine_bundle_hash"], absolute["engine_bundle_hash"])
        (roots[1] / "engine" / "b.py").write_text("VALUE = 3\n", encoding="utf-8")
        changed = compute_engine_bundle_identity(("engine/a.py", "engine/b.py"), project_root=roots[1])
        self.assertNotEqual(first["engine_bundle_hash"], changed["engine_bundle_hash"])

    def test_writer_append_duplicate_semantics_and_single_line_json(self) -> None:
        decision = self._decision()
        self.assertTrue(self.writer.append_record(decision)["appended"])
        self.assertTrue(self.writer.append_record(decision)["duplicate"])
        second_decision = self._decision(record_id="44444444-4444-4444-8444-444444444444")
        self.assertTrue(self.writer.append_record(second_decision)["duplicate"])
        approval = self._stage_record("APPROVAL")
        self.assertTrue(self.writer.append_record(approval)["appended"])
        lines = (self.live_dir / "2026-08-08.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(2, len(lines))
        self.assertTrue(all(isinstance(json.loads(line), dict) for line in lines))

    def test_writer_rejects_invalid_without_writing_and_reports_failure(self) -> None:
        invalid = self._decision()
        invalid["environment"] = "INVALID"
        self.assertTrue(self.writer.append_record(invalid)["invalid"])
        self.assertFalse(self.live_dir.exists())
        blocked = self.root / "blocked"
        blocked.write_text("file", encoding="utf-8")
        writer = DecisionTraceWriter(blocked)
        self.assertTrue(writer.append_record(self._decision())["write_failed"])

    def test_backtest_writer_path_is_explicit_and_does_not_touch_runtime(self) -> None:
        record = self._decision(trace_level="BACKTEST")
        record.update(environment="BACKTEST", backtest_run_id="run-1")
        result = self.writer.append_record(record)
        self.assertTrue(result["appended"])
        self.assertEqual(self.root / "backtests" / "run-1" / "decision_trace.jsonl", Path(result["path"]))

    def test_reader_trace_bundle_filters_and_time_range(self) -> None:
        self.writer.append_record(self._decision())
        self.writer.append_record(self._stage_record("APPROVAL"))
        other = self._stage_record(
            "POLICY",
            trace_id="55555555-5555-4555-8555-555555555555",
            record_id="66666666-6666-4666-8666-666666666666",
            recorded_at="2026-08-08T10:00:00+09:00",
        )
        other["stock_code"] = "000660"
        other["routine_instance_id"] = "routine-2"
        self.writer.append_record(other)
        self.assertEqual(["DECISION", "APPROVAL"], [item["stage"] for item in self.reader.read_trace(TRACE_ID)["records"]])
        self.assertEqual(2, len(self.reader.read_records(stock_code="005930")["records"]))
        self.assertEqual(1, len(self.reader.read_records(routine_instance_id="routine-2")["records"]))
        self.assertEqual(1, len(self.reader.read_records(stage="POLICY")["records"]))
        self.assertEqual(2, len(self.reader.read_records(environment="LIVE", trace_level="NORMAL", end_at="2026-08-08T09:30:00+09:00")["records"]))

    def test_reader_skips_malformed_invalid_missing_and_access_error(self) -> None:
        self.writer.append_record(self._decision())
        path = self.live_dir / "2026-08-08.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{broken\n")
            handle.write(json.dumps({"schema_version": SCHEMA_VERSION}) + "\n")
        result = self.reader.read_records()
        self.assertEqual(1, result["malformed_count"])
        self.assertEqual(1, result["invalid_count"])
        self.assertEqual(1, len(result["records"]))
        self.assertEqual([], DecisionTraceReader(self.root / "missing").read_records()["records"])
        with patch("pathlib.Path.open", side_effect=OSError("read denied")):
            failed = self.reader.read_records()
        self.assertEqual(1, len(failed["errors"]))
        self.assertEqual([], failed["records"])

    def test_diagnostic_mode_scope_duration_expiration_and_normal_default(self) -> None:
        now = datetime(2026, 8, 8, 9, 0, tzinfo=timezone(timedelta(hours=9)))
        normal = resolve_live_trace_mode(activated_at=now)
        self.assertTrue(normal["accepted"])
        self.assertEqual("NORMAL", normal["trace_level"])
        self.assertFalse(resolve_live_trace_mode(diagnostic_enabled=True, activated_at=now)["accepted"])
        stock = resolve_live_trace_mode(diagnostic_enabled=True, stock_scope="005930", activated_at=now)
        routine = resolve_live_trace_mode(diagnostic_enabled=True, routine_scope="routine-1", activated_at=now)
        self.assertEqual(30, stock["minutes"])
        self.assertTrue(routine["accepted"])
        self.assertFalse(resolve_live_trace_mode(diagnostic_enabled=True, stock_scope="005930", minutes=121, activated_at=now)["accepted"])
        self.assertFalse(live_trace_mode_expired(stock, now=now + timedelta(minutes=29)))
        self.assertTrue(live_trace_mode_expired(stock, now=now + timedelta(minutes=30)))


if __name__ == "__main__":
    unittest.main()
