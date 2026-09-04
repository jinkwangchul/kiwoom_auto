# -*- coding: utf-8 -*-

from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from real_trade_retirement_migration import (
    APPROVED_TARGET_CODES,
    CONFIG_LEGACY_FIELDS,
    RESULT_ALREADY_MIGRATED,
    RESULT_APPLICATION_ACTIVE,
    RESULT_BLOCKED_OBLIGATION,
    RESULT_MIGRATED,
    RESULT_PREVIEW_STALE,
    RESULT_TARGET_NOT_APPROVED,
    MigrationTargetSpec,
    RealTradeRetirementMigration,
    _canonical_sha256,
    _sha256_bytes,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class RealTradeRetirementMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.specs = self._build_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_fixture(
        self,
        *,
        holding_by_code: dict[str, int] | None = None,
        review_by_code: dict[str, bool] | None = None,
    ) -> tuple[MigrationTargetSpec, ...]:
        holding_by_code = holding_by_code or {}
        review_by_code = review_by_code or {}
        identities = (
            ("005070", "코스모신소재", "", None, False, "MONITORING", True),
            ("012210", "삼미금속", "", None, True, "MONITORING", True),
            (
                "032680",
                "소프트센",
                "38d63d8c-40b6-41d4-a094-afe56ac39df9",
                False,
                False,
                "STOPPED",
                False,
            ),
        )
        specs: list[MigrationTargetSpec] = []
        for code, name, assignment, enabled, excluded, status, state_field in identities:
            config = {
                "stock_code": code,
                "stock_name": name,
                "assigned_routine_instance_id": assignment,
                "operation_excluded": excluded,
                "real_trade_enabled": False,
                "real_trade_policy_updated_at": "2026-08-01 01:02:03",
                "unrelated_config": {"preserve": code},
            }
            state = {
                "status": status,
                "updated_at": "2026-08-02 03:04:05",
                "holding_qty": holding_by_code.get(code, 0),
                "avg_price": 0,
                "review_required": review_by_code.get(code, False),
                "signal_probe_only": True,
                "unrelated_state": {"preserve": code},
            }
            if state_field:
                state["real_trade_enabled"] = False

            stock_dir = self.root / "stocks" / f"{code}_{name}"
            config_path = stock_dir / "config.json"
            state_path = stock_dir / "state.json"
            _write_json(config_path, config)
            _write_json(state_path, state)
            _write_json(stock_dir / "orders.json", {"orders": []})

            config_after = dict(config)
            for field in CONFIG_LEGACY_FIELDS:
                config_after.pop(field)
            state_after = dict(state)
            state_after.pop("real_trade_enabled", None)
            specs.append(
                MigrationTargetSpec(
                    stock_code=code,
                    stock_name=name,
                    config_raw_sha256=_file_sha(config_path),
                    state_raw_sha256=_file_sha(state_path),
                    config_canonical_before=_canonical_sha256(config),
                    config_canonical_after=_canonical_sha256(config_after),
                    state_canonical_before=_canonical_sha256(state),
                    state_canonical_after=_canonical_sha256(state_after),
                    assigned_routine_instance_id=assignment,
                    instance_enabled=enabled,
                    operation_excluded=excluded,
                    status=status,
                )
            )
            if assignment:
                _write_json(
                    self.root / "routine_instances" / assignment / "instance.json",
                    {"instance_id": assignment, "enabled": enabled},
                )

        _write_json(
            self.root / "stocks" / "111111_TRUE" / "config.json",
            {"stock_code": "111111", "real_trade_enabled": True},
        )
        _write_json(
            self.root / "runtime" / "operation_state.json",
            {
                "operation_status": "RUNNING",
                "operation_participant_stock_codes": ["000660", "005070", "218410"],
            },
        )
        _write_json(self.root / "runtime" / "order_queue.json", {"orders": []})
        _write_json(
            self.root / "runtime" / "order_executions.json", {"executions": []}
        )
        _write_json(self.root / "runtime" / "order_locks.json", {"locks": []})
        _write_json(self.root / "runtime" / "positions.json", {"positions": []})
        _write_json(
            self.root / "runtime" / "broker_holdings.json",
            {"holdings": [], "production_recovery_reviews": []},
        )
        _write_json(
            self.root / "mock_validation" / "runtime" / "current_sessions.json",
            {"current_by_stock": {}},
        )
        return tuple(specs)

    def _migration(self, **kwargs: object) -> RealTradeRetirementMigration:
        return RealTradeRetirementMigration(
            self.root,
            target_specs=kwargs.pop("target_specs", self.specs),
            application_active_check=kwargs.pop("application_active_check", lambda: False),
            **kwargs,
        )

    def test_migrates_only_allowlisted_fields_and_preserves_all_authorities(self) -> None:
        operation_path = self.root / "runtime" / "operation_state.json"
        true_path = self.root / "stocks" / "111111_TRUE" / "config.json"
        operation_before = operation_path.read_bytes()
        true_before = true_path.read_bytes()

        dry_run = self._migration().dry_run()
        self.assertTrue(dry_run["ok"])
        self.assertEqual("READY", dry_run["reason_code"])
        result = self._migration().migrate()
        self.assertTrue(result["ok"])
        self.assertEqual(RESULT_MIGRATED, result["reason_code"])

        expected = {
            "005070": ("", False, "MONITORING"),
            "012210": ("", True, "MONITORING"),
            "032680": (
                "38d63d8c-40b6-41d4-a094-afe56ac39df9",
                False,
                "STOPPED",
            ),
        }
        for spec in self.specs:
            stock_dir = next((self.root / "stocks").glob(f"{spec.stock_code}_*"))
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            state = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))
            assignment, excluded, status = expected[spec.stock_code]
            self.assertNotIn("real_trade_enabled", config)
            self.assertNotIn("real_trade_policy_updated_at", config)
            self.assertNotIn("real_trade_enabled", state)
            self.assertEqual(assignment, config["assigned_routine_instance_id"])
            self.assertIs(excluded, config["operation_excluded"])
            self.assertEqual(status, state["status"])
            self.assertEqual("2026-08-02 03:04:05", state["updated_at"])
            self.assertEqual({"preserve": spec.stock_code}, state["unrelated_state"])

        self.assertEqual(operation_before, operation_path.read_bytes())
        self.assertEqual(true_before, true_path.read_bytes())
        self.assertFalse(result["results"][2]["state_changed"])

        hashes_before_retry = {
            path: _file_sha(path)
            for path in self.root.rglob("*.json")
        }
        retry = self._migration().migrate()
        self.assertTrue(retry["ok"])
        self.assertEqual(RESULT_ALREADY_MIGRATED, retry["reason_code"])
        self.assertEqual(
            hashes_before_retry,
            {path: _file_sha(path) for path in self.root.rglob("*.json")},
        )

    def test_preview_stale_blocks_every_target_without_writes(self) -> None:
        path = next((self.root / "stocks").glob("005070_*")) / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["unexpected_drift"] = True
        _write_json(path, config)
        before = {item: item.read_bytes() for item in self.root.rglob("*.json")}
        result = self._migration().migrate()
        self.assertFalse(result["ok"])
        self.assertEqual(RESULT_PREVIEW_STALE, result["reason_code"])
        self.assertEqual(before, {item: item.read_bytes() for item in self.root.rglob("*.json")})

    def test_application_active_blocks_before_data_read_or_write(self) -> None:
        result = self._migration(application_active_check=lambda: True).migrate()
        self.assertFalse(result["ok"])
        self.assertEqual(RESULT_APPLICATION_ACTIVE, result["reason_code"])

    def test_unapproved_target_is_rejected(self) -> None:
        unapproved = replace(self.specs[0], stock_code="999999")
        result = self._migration(target_specs=(unapproved,)).migrate()
        self.assertFalse(result["ok"])
        self.assertEqual(RESULT_TARGET_NOT_APPROVED, result["reason_code"])
        self.assertEqual(
            {"005070", "012210", "032680"},
            set(APPROVED_TARGET_CODES),
        )

    def test_obligation_sources_block_before_any_migration(self) -> None:
        cases = (
            ("order_queue.json", "orders", {"stock_code": "005070"}, "QUEUE_ACTIVE"),
            (
                "order_executions.json",
                "executions",
                {"stock_code": "012210"},
                "EXECUTION_ACTIVE",
            ),
            ("order_locks.json", "locks", {"stock_code": "032680"}, "LOCK_ACTIVE"),
        )
        for filename, key, record, reason in cases:
            with self.subTest(reason=reason):
                path = self.root / "runtime" / filename
                original = path.read_bytes()
                _write_json(path, {key: [record]})
                result = self._migration().migrate()
                self.assertFalse(result["ok"])
                self.assertEqual(RESULT_BLOCKED_OBLIGATION, result["reason_code"])
                self.assertIn(reason, result["targets"][0 if record["stock_code"] == "005070" else 1 if record["stock_code"] == "012210" else 2]["blockers"])
                path.write_bytes(original)

        mock_path = self.root / "mock_validation" / "runtime" / "current_sessions.json"
        _write_json(mock_path, {"current_by_stock": {"005070": "MV-1"}})
        result = self._migration().migrate()
        self.assertFalse(result["ok"])
        self.assertIn("MOCK_SESSION_ACTIVE", result["targets"][0]["blockers"])

    def test_holding_review_and_stock_order_block(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.specs = self._build_fixture(holding_by_code={"005070": 1})
        result = self._migration().migrate()
        self.assertEqual(RESULT_BLOCKED_OBLIGATION, result["reason_code"])
        self.assertIn("HOLDING_EXISTS", result["targets"][0]["blockers"])

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.specs = self._build_fixture(review_by_code={"012210": True})
        result = self._migration().migrate()
        self.assertEqual(RESULT_BLOCKED_OBLIGATION, result["reason_code"])
        self.assertIn("REVIEW_REQUIRED", result["targets"][1]["blockers"])

        stock_dir = next((self.root / "stocks").glob("032680_*"))
        _write_json(stock_dir / "orders.json", {"orders": [{"state": "CANCEL_PENDING"}]})
        result = self._migration().migrate()
        self.assertEqual(RESULT_BLOCKED_OBLIGATION, result["reason_code"])
        self.assertIn("PENDING_ORDER", result["targets"][2]["blockers"])

    def test_partial_config_completion_resumes_at_state_boundary(self) -> None:
        stock_dir = next((self.root / "stocks").glob("005070_*"))
        config_path = stock_dir / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for field in CONFIG_LEGACY_FIELDS:
            config.pop(field)
        _write_json(config_path, config)
        config_hash = _file_sha(config_path)

        result = self._migration().migrate()
        self.assertTrue(result["ok"])
        first = result["results"][0]
        self.assertFalse(first["config_changed"])
        self.assertTrue(first["state_changed"])
        self.assertEqual(config_hash, _file_sha(config_path))

    def test_module_has_no_broker_or_production_order_mutation_dependency(self) -> None:
        source = (Path(__file__).parents[1] / "real_trade_retirement_migration.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden = {
            "kiwoom_send_order_executor",
            "routine_signal_queue",
            "execution_queue_writer",
            "chejan_event_recorder",
        }
        self.assertTrue(forbidden.isdisjoint(imported))
        self.assertNotIn("SendOrder", source)


if __name__ == "__main__":
    unittest.main()
