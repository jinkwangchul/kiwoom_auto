# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import (
    INSTANCE_ERROR,
    MockValidationError,
    instance_effective_settings,
    payload_hash,
    validate_instance_effective_settings,
    validate_reference_snapshot,
)
from mock_validation_reference_snapshot import build_mock_reference_snapshot
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Clock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"2026-09-03T09:00:{self.index:02d}+09:00"


def _reference() -> dict:
    instances = [
        {
            "instance_id": value,
            "definition_id": "indicator-follow",
            "routine_type": "INDICATOR_FOLLOW",
            "display_name": f"루틴 {value}",
            "group_id": "group-1",
        }
        for value in ("A", "B", "C")
    ]
    return build_mock_reference_snapshot(
        stock={"code": "005930", "name": "삼성전자", "stock_path": "stocks/005930_삼성전자"},
        routine_instances=instances,
        rules_by_instance_id={value: {"version": 1, "instance": value} for value in ("A", "B", "C")},
        created_at="2026-09-03T08:59:00+09:00",
    )


def _display_contract() -> dict:
    return {
        "initial_buy": {
            "mode": "QUANTITY",
            "badge": "주수",
            "value": 1,
            "value_text": "1주",
        },
        "operation_schedule": {"display_text": "09:00~13:30"},
        "liquidation": {"display_text": "5분/시장가"},
    }


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _tree_hashes(*roots: Path) -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        if root.exists()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MockValidationFoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "mock_validation"
        self.repository = MockValidationRepository(self.root)
        self.clock = _Clock()
        self.service = MockValidationSessionService(self.repository, now_factory=self.clock)
        self.session_id = "MV-00000000000000000000000000000001"

    def create(self, session_id: str | None = None):
        return self.service.create_stock_session(
            reference_snapshot=_reference(),
            validation_session_id=session_id or self.session_id,
            command_id=f"MC-create-{session_id or self.session_id}",
        )

    def start(self):
        self.create()
        return self.service.start_stock_mock_session(self.session_id, command_id="MC-start")

    def test_session_creation_is_waiting_and_contains_all_instances(self) -> None:
        result = self.create()
        document = result["document"]
        self.assertTrue(result["created"])
        self.assertEqual("WAITING", document["session"]["state"])
        self.assertEqual({"A", "B", "C"}, set(document["instance_execution"]))
        self.assertTrue(document["session"]["mock_tax_enabled"])
        self.assertEqual(0.002, document["session"]["mock_tax_rate"])

    def test_session_creation_persists_caller_resolved_production_defaults(self) -> None:
        settings = {
            instance_id: {
                "initial_buy": {"mode": "AMOUNT", "value": 765_432},
                "operation_schedule": {
                    "start_time": "10:17:00",
                    "end_buy_time": "14:23:00",
                },
                "operation_mode": "CONTINUOUS",
                "manual_ats": {
                    "selected_sessions": [],
                },
            }
            for instance_id in ("A", "B", "C")
        }

        created = self.service.create_stock_session(
            reference_snapshot=_reference(),
            effective_settings_by_instance=settings,
            validation_session_id=self.session_id,
            command_id="MC-create-provider-defaults",
        )["document"]

        self.assertEqual(settings, created["effective_settings_by_instance"])
        self.assertEqual(
            settings,
            MockValidationRepository(self.root).read_session(self.session_id)[
                "effective_settings_by_instance"
            ],
        )

    def test_legacy_execution_method_is_preserved_as_storage_evidence_but_ignored(self) -> None:
        settings = {
            "initial_buy": {"mode": "QUANTITY", "value": 3},
            "operation_schedule": {
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            "operation_mode": "CONTINUOUS",
            "manual_ats": {
                "selected_sessions": ["extra1"],
                "execution_method": "MARKET",
            },
        }

        stored = validate_instance_effective_settings(
            settings,
            preserve_legacy_representation=True,
        )
        effective = validate_instance_effective_settings(settings)

        self.assertEqual("MARKET", stored["manual_ats"]["execution_method"])
        self.assertNotIn("execution_method", effective["manual_ats"])

    def test_instance_effective_settings_are_mutable_waiting_only_and_restart_safe(self) -> None:
        created = self.create()["document"]
        frozen_hash = created["reference_snapshot"]["snapshot_hash"]
        sibling_before = deepcopy(instance_effective_settings(created, "B"))
        sibling_state_before = payload_hash(
            {
                "settings": sibling_before,
                "execution": created["instance_execution"]["B"],
                "position": next(
                    item for item in created["positions"]
                    if item["routine_instance_id"] == "B"
                ),
                "cycle": created["cycle_state_by_instance"]["B"],
                "progression": created["progression_by_instance"]["B"],
            }
        )
        changed = self.service.set_instance_effective_settings(
            self.session_id,
            routine_instance_id="A",
            initial_buy={"mode": "AMOUNT", "value": 500_000},
            operation_schedule={
                "start_time": "10:15:00",
                "end_buy_time": "14:05:00",
            },
            operation_mode="CONTINUOUS",
            manual_ats={
                "selected_sessions": ["extra2"],
            },
            command_id="MC-settings-A",
        )["document"]
        expected = {
            "initial_buy": {"mode": "AMOUNT", "value": 500_000},
            "operation_schedule": {
                "start_time": "10:15:00",
                "end_buy_time": "14:05:00",
            },
            "operation_mode": "CONTINUOUS",
            "manual_ats": {
                "selected_sessions": ["extra2"],
            },
        }
        self.assertEqual(expected, instance_effective_settings(changed, "A"))
        self.assertEqual(sibling_before, instance_effective_settings(changed, "B"))
        self.assertEqual(
            sibling_state_before,
            payload_hash(
                {
                    "settings": instance_effective_settings(changed, "B"),
                    "execution": changed["instance_execution"]["B"],
                    "position": next(
                        item for item in changed["positions"]
                        if item["routine_instance_id"] == "B"
                    ),
                    "cycle": changed["cycle_state_by_instance"]["B"],
                    "progression": changed["progression_by_instance"]["B"],
                }
            ),
        )
        self.assertEqual(frozen_hash, changed["reference_snapshot"]["snapshot_hash"])

        restarted = MockValidationRepository(self.root).read_session(self.session_id)
        self.assertEqual(expected, instance_effective_settings(restarted, "A"))
        self.service.start_stock_mock_session(self.session_id, command_id="MC-start-settings")
        with self.assertRaisesRegex(
            MockValidationError,
            "MOCK_INSTANCE_SETTINGS_REQUIRE_WAITING",
        ):
            self.service.set_instance_effective_settings(
                self.session_id,
                routine_instance_id="A",
                initial_buy={"mode": "QUANTITY", "value": 3},
                command_id="MC-running-edit",
            )

    def test_legacy_manual_modes_project_canonically_without_read_backfill(self) -> None:
        self.create()
        session_path = self.root / "runtime" / "sessions" / f"{self.session_id}.json"
        raw = json.loads(session_path.read_text(encoding="utf-8"))
        for instance_id, legacy_mode in (("A", "MANUAL"), ("B", "MANUAL_ATS")):
            settings = raw["effective_settings_by_instance"][instance_id]
            settings["operation_mode"] = legacy_mode
            settings.pop("manual_ats", None)
        session_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        before = session_path.read_bytes()

        projected = self.repository.read_session(self.session_id)

        settings_a = instance_effective_settings(projected, "A")
        settings_b = instance_effective_settings(projected, "B")
        self.assertEqual("CONTINUOUS", settings_a["operation_mode"])
        self.assertEqual([], settings_a["manual_ats"]["selected_sessions"])
        self.assertEqual("CONTINUOUS", settings_b["operation_mode"])
        self.assertEqual(
            ["extra1", "extra2", "extra3"],
            settings_b["manual_ats"]["selected_sessions"],
        )
        self.assertEqual(before, session_path.read_bytes())

        changed = self.service.set_instance_effective_settings(
            self.session_id,
            routine_instance_id="A",
            initial_buy={"mode": "QUANTITY", "value": 7},
            command_id="MC-canonicalize-edited-legacy-A",
        )["document"]
        self.assertEqual("CONTINUOUS", changed["effective_settings_by_instance"]["A"]["operation_mode"])
        self.assertIn("manual_ats", changed["effective_settings_by_instance"]["A"])
        self.assertEqual("MANUAL_ATS", changed["effective_settings_by_instance"]["B"]["operation_mode"])
        self.assertNotIn("manual_ats", changed["effective_settings_by_instance"]["B"])

    def test_instance_reset_preserves_mutable_effective_settings(self) -> None:
        self.create()
        expected = self.service.set_instance_effective_settings(
            self.session_id,
            routine_instance_id="B",
            initial_buy={"mode": "QUANTITY", "value": 3},
            operation_schedule={
                "start_time": "10:30:00",
                "end_buy_time": "13:45:00",
            },
            operation_mode="MANUAL",
            command_id="MC-settings-B",
        )["document"]["effective_settings_by_instance"]["B"]
        self.service.start_stock_mock_session(self.session_id, command_id="MC-start-reset-settings")
        self.service.set_instance_position(
            self.session_id,
            "B",
            holding_qty=4,
            available_qty=4,
            average_price=100,
            realized_cost_basis=400,
            command_id="MC-position-reset-settings",
        )
        self.service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="B",
            reason_code="MOCK_SETTINGS_RESET_TEST",
            reason="reset preserves settings",
            command_id="MC-error-reset-settings",
        )
        self.assertEqual(
            INSTANCE_ERROR,
            self.repository.read_session(self.session_id)["instance_execution"]["B"]["state"],
        )
        result = self.service.reset_routine_instance(
            self.session_id,
            routine_instance_id="B",
            command_id="MC-reset-settings-B",
        )["document"]
        position = next(
            item for item in result["positions"]
            if item["routine_instance_id"] == "B"
        )
        self.assertEqual("WAITING", result["instance_execution"]["B"]["state"])
        self.assertEqual((0, 0, 0), (
            position["holding_qty"],
            position["average_price"],
            position["realized_cost_basis"],
        ))
        self.assertEqual(expected, instance_effective_settings(result, "B"))

    def test_frozen_display_contract_is_copied_hashed_and_persisted(self) -> None:
        source = _display_contract()
        reference = build_mock_reference_snapshot(
            stock={"code": "005930", "name": "삼성전자", "stock_path": "stocks/005930_삼성전자"},
            routine_instances=[
                {
                    "instance_id": "A",
                    "definition_id": "indicator-follow",
                    "routine_type": "INDICATOR_FOLLOW",
                    "display_name": "루틴 A",
                }
            ],
            rules_by_instance_id={"A": {"version": 1}},
            display_contract=source,
            created_at="2026-09-03T08:59:00+09:00",
        )
        source["initial_buy"]["value"] = 99
        source["operation_schedule"]["display_text"] = "10:00~12:00"

        self.assertEqual(_display_contract(), reference["display_contract"])
        self.assertEqual(
            payload_hash({key: value for key, value in reference.items() if key != "snapshot_hash"}),
            reference["snapshot_hash"],
        )
        created = self.service.create_stock_session(
            reference_snapshot=reference,
            validation_session_id=self.session_id,
            command_id="MC-create-display-contract",
        )["document"]
        reloaded = self.repository.read_session(self.session_id)
        self.assertEqual(_display_contract(), created["reference_snapshot"]["display_contract"])
        self.assertEqual(_display_contract(), reloaded["reference_snapshot"]["display_contract"])

    def test_display_contract_tamper_and_invalid_semantics_fail_closed(self) -> None:
        reference = build_mock_reference_snapshot(
            stock={"code": "005930", "name": "삼성전자", "stock_path": "stocks/005930_삼성전자"},
            routine_instances=[
                {
                    "instance_id": "A",
                    "definition_id": "indicator-follow",
                    "routine_type": "INDICATOR_FOLLOW",
                    "display_name": "루틴 A",
                }
            ],
            rules_by_instance_id={"A": {"version": 1}},
            display_contract=_display_contract(),
            created_at="2026-09-03T08:59:00+09:00",
        )
        tampered = deepcopy(reference)
        tampered["display_contract"]["initial_buy"]["value"] = 7
        with self.assertRaisesRegex(MockValidationError, "MOCK_REFERENCE_SNAPSHOT_HASH_MISMATCH"):
            validate_reference_snapshot(tampered)

        invalid = _display_contract()
        invalid["initial_buy"]["badge"] = "금액"
        with self.assertRaisesRegex(MockValidationError, "MOCK_INITIAL_BUY_BADGE_INVALID"):
            build_mock_reference_snapshot(
                stock={"code": "005930", "name": "삼성전자", "stock_path": "stocks/005930_삼성전자"},
                routine_instances=[
                    {
                        "instance_id": "A",
                        "definition_id": "indicator-follow",
                        "routine_type": "INDICATOR_FOLLOW",
                        "display_name": "루틴 A",
                    }
                ],
                rules_by_instance_id={"A": {"version": 1}},
                display_contract=invalid,
                created_at="2026-09-03T08:59:00+09:00",
            )

    def test_common_tax_settings_missing_file_uses_canonical_default(self) -> None:
        settings_path = self.root / "settings.json"

        self.assertFalse(settings_path.exists())
        self.assertEqual(
            {
                "schema_version": "mock_validation_settings_v1",
                "revision": 0,
                "mock_tax_enabled": True,
                "mock_tax_rate": 0.002,
            },
            self.repository.read_settings(),
        )
        self.assertFalse(settings_path.exists())

    def test_common_tax_settings_persist_enabled_and_rate_across_repository_restart(self) -> None:
        saved = self.repository.write_settings(
            mock_tax_enabled=False,
            mock_tax_rate=0.0037,
            updated_at="2026-09-03T09:00:00+09:00",
        )

        self.assertTrue(saved["changed"])
        restarted = MockValidationRepository(self.root)
        settings = restarted.read_settings()
        self.assertFalse(settings["mock_tax_enabled"])
        self.assertEqual(0.0037, settings["mock_tax_rate"])
        self.assertEqual(1, settings["revision"])

    def test_saving_initial_default_materializes_settings_sot(self) -> None:
        settings_path = self.root / "settings.json"

        saved = self.repository.write_settings(
            mock_tax_enabled=True,
            mock_tax_rate=0.002,
        )

        self.assertTrue(saved["changed"])
        self.assertTrue(settings_path.is_file())
        self.assertEqual(1, self.repository.read_settings()["revision"])

    def test_new_session_inherits_latest_common_tax_settings(self) -> None:
        self.repository.write_settings(
            mock_tax_enabled=False,
            mock_tax_rate=0.0043,
        )

        document = self.create()["document"]

        self.assertFalse(document["session"]["mock_tax_enabled"])
        self.assertEqual(0.0043, document["session"]["mock_tax_rate"])

    def test_session_creation_ignores_production_account_state_and_starts_zero(self) -> None:
        production_state = {
            "holding_qty": 100,
            "average_price": 70_000,
            "pending_orders": [{"order_no": "REAL-1", "remaining_qty": 25}],
            "realized_pnl": 12_345,
            "unrealized_pnl": 67_890,
            "account_cash": 9_999_999,
        }
        reference = build_mock_reference_snapshot(
            stock={
                "code": "005930",
                "name": "삼성전자",
                "stock_path": "stocks/005930_삼성전자",
                **production_state,
            },
            routine_instances=[
                {
                    "instance_id": "A",
                    "definition_id": "indicator-follow",
                    "routine_type": "INDICATOR_FOLLOW",
                    "display_name": "루틴 A",
                    "group_id": "group-1",
                }
            ],
            rules_by_instance_id={"A": {"version": 1}},
            created_at="2026-09-03T08:59:00+09:00",
        )

        document = self.service.create_stock_session(
            reference_snapshot=reference,
            validation_session_id=self.session_id,
            command_id="MC-create-zero-base",
        )["document"]

        self.assertEqual([], document["orders"])
        self.assertEqual([], document["fills"])
        self.assertEqual(0, document["positions"][0]["holding_qty"])
        self.assertEqual(0, document["positions"][0]["average_price"])
        self.assertEqual(0, document["pnl"][0]["realized_pnl"])
        self.assertEqual(0, document["pnl"][0]["unrealized_pnl"])
        self.assertFalse(
            set(production_state).intersection(document["reference_snapshot"])
        )

    def test_stock_start_uses_one_timestamp_and_has_no_instance_start_api(self) -> None:
        result = self.start()
        document = result["document"]
        started = document["session"]["started_at"]
        self.assertTrue(started)
        self.assertEqual({started}, {item["started_at"] for item in document["instance_execution"].values()})
        self.assertEqual({True}, {item["progression_allowed"] for item in document["instance_execution"].values()})
        self.assertFalse(hasattr(self.service, "start_instance"))

    def test_instance_order_position_and_pnl_are_isolated(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id,
            routine_instance_id="A",
            side="BUY",
            order_type="LIMIT",
            requested_qty=3,
            requested_price=70000,
            command_id="MC-order-A",
        )["order"]
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=3, available_qty=3,
            average_price=70000, realized_cost_basis=210000, command_id="MC-pos-A",
        )
        self.service.set_instance_pnl(
            self.session_id, "A", realized_pnl=100, unrealized_pnl=50,
            gross_pnl=150, commission=10, mock_tax=20, net_pnl=120,
            command_id="MC-pnl-A",
        )
        document = self.repository.read_session(self.session_id)
        self.assertEqual("A", order["routine_instance_id"])
        self.assertFalse(any(item["routine_instance_id"] in {"B", "C"} for item in document["orders"]))
        positions = {item["routine_instance_id"]: item for item in document["positions"]}
        pnl = {item["routine_instance_id"]: item for item in document["pnl"]}
        self.assertEqual(3, positions["A"]["holding_qty"])
        self.assertEqual(0, positions["B"]["holding_qty"])
        self.assertEqual(0, positions["C"]["holding_qty"])
        self.assertEqual(120, pnl["A"]["net_pnl"])
        self.assertEqual(0, pnl["B"]["net_pnl"])
        self.assertEqual(0, pnl["C"]["net_pnl"])

    def test_instance_error_isolates_only_source_instance(self) -> None:
        self.start()
        result = self.service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="B",
            reason_code="MOCK_TEST_ERROR",
            reason="B failure",
            command_id="MC-error-B",
        )
        document = result["document"]
        self.assertEqual("RUNNING", document["session"]["state"])
        self.assertFalse(document["review"]["review_required"])
        self.assertEqual(INSTANCE_ERROR, document["instance_execution"]["B"]["state"])
        self.assertFalse(document["instance_execution"]["B"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["A"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["C"]["progression_allowed"])
        created = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=1, command_id="MC-after-instance-error",
        )
        self.assertEqual("A", created["order"]["routine_instance_id"])
        with self.assertRaisesRegex(MockValidationError, "MOCK_INSTANCE_PROGRESSION_BLOCKED"):
            self.service.create_order(
                self.session_id, routine_instance_id="B", side="BUY", order_type="LIMIT",
                requested_qty=1, requested_price=1, command_id="MC-blocked-instance",
            )
        event_types = [item["event_type"] for item in self.repository.read_events(self.session_id)]
        self.assertIn("INSTANCE_ERROR", event_types)
        self.assertNotIn("SESSION_REVIEW_STOPPED", event_types)

    def test_instance_reset_zeroes_only_target_and_preserves_siblings(self) -> None:
        self.start()
        for instance_id, quantity in (("A", 1), ("B", 2), ("C", 3)):
            order = self.service.create_order(
                self.session_id,
                routine_instance_id=instance_id,
                side="BUY",
                order_type="LIMIT",
                requested_qty=quantity,
                requested_price=10,
                command_id=f"MC-order-{instance_id}",
            )["order"]
            self.service.transition_order(
                self.session_id,
                order["mock_order_id"],
                "OPEN",
                command_id=f"MC-open-{instance_id}",
            )
            if instance_id == "B":
                self.service.append_fill(
                    self.session_id,
                    mock_order_id=order["mock_order_id"],
                    qty=1,
                    price=10,
                    market_snapshot_identity="MKT-reset-B",
                    command_id="MC-fill-B",
                )
            self.service.set_instance_position(
                self.session_id,
                instance_id,
                holding_qty=quantity,
                available_qty=quantity,
                average_price=10,
                realized_cost_basis=quantity * 10,
                command_id=f"MC-position-{instance_id}",
            )
        self.service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="B",
            reason_code="MOCK_TEST_ERROR",
            reason="B failure",
            command_id="MC-error-B-reset",
        )
        before = self.repository.read_session(self.session_id)

        def sibling_state(document, instance_id):
            return payload_hash({
                "orders": [item for item in document["orders"] if item["routine_instance_id"] == instance_id],
                "fills": [item for item in document["fills"] if item["routine_instance_id"] == instance_id],
                "position": next(item for item in document["positions"] if item["routine_instance_id"] == instance_id),
                "pnl": next(item for item in document["pnl"] if item["routine_instance_id"] == instance_id),
                "execution": document["instance_execution"][instance_id],
                "cycle": document["cycle_state_by_instance"][instance_id],
                "progression": document["progression_by_instance"][instance_id],
            })

        sibling_hashes = {key: sibling_state(before, key) for key in ("A", "C")}
        events_before = self.repository.read_events(self.session_id)
        result = self.service.reset_routine_instance(
            self.session_id,
            routine_instance_id="B",
            command_id="MC-reset-B",
        )
        document = result["document"]
        self.assertFalse(any(item["routine_instance_id"] == "B" for item in document["orders"]))
        self.assertFalse(any(item["routine_instance_id"] == "B" for item in document["fills"]))
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "B")
        pnl = next(item for item in document["pnl"] if item["routine_instance_id"] == "B")
        self.assertEqual((0, 0, 0), (position["holding_qty"], position["average_price"], pnl["net_pnl"]))
        self.assertEqual("WAITING", document["instance_execution"]["B"]["state"])
        self.assertFalse(document["instance_execution"]["B"]["error_code"])
        self.assertEqual(sibling_hashes, {key: sibling_state(document, key) for key in ("A", "C")})
        events_after = self.repository.read_events(self.session_id)
        self.assertEqual(len(events_before) + 1, len(events_after))
        self.assertEqual("INSTANCE_RESET", events_after[-1]["event_type"])

    def test_reset_zeroes_all_current_state_preserves_history_and_does_not_start(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=2, requested_price=10, command_id="MC-order",
        )["order"]
        self.service.transition_order(self.session_id, order["mock_order_id"], "OPEN", command_id="MC-open")
        self.service.append_fill(
            self.session_id, mock_order_id=order["mock_order_id"], qty=1, price=10,
            market_snapshot_identity="MKT-1", command_id="MC-fill",
        )
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=1, available_qty=1,
            average_price=10, realized_cost_basis=10, command_id="MC-pos",
        )
        self.service.stop_for_instance_error(
            self.session_id, source_routine_instance_id="B", reason_code="ERR",
            reason="failure", command_id="MC-error",
        )
        events_before = self.repository.read_events(self.session_id)
        result = self.service.reset_stock_session(self.session_id, command_id="MC-reset")
        document = result["document"]
        self.assertEqual("WAITING", document["session"]["state"])
        self.assertEqual(2, document["session"]["session_generation"])
        self.assertEqual([], document["orders"])
        self.assertEqual([], document["fills"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in document["positions"]))
        self.assertTrue(all(item["net_pnl"] == 0 for item in document["pnl"]))
        self.assertFalse(document["review"]["review_required"])
        self.assertEqual({False}, {item["progression_allowed"] for item in document["instance_execution"].values()})
        events_after = self.repository.read_events(self.session_id)
        self.assertEqual(len(events_before) + 1, len(events_after))
        self.assertEqual("SESSION_RESET", events_after[-1]["event_type"])

    def test_end_archives_immutable_history_and_new_session_starts_zero(self) -> None:
        self.start()
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=5, available_qty=5,
            average_price=100, realized_cost_basis=500, command_id="MC-pos-v1",
        )
        ended = self.service.end_stock_session(self.session_id, command_id="MC-end-v1")
        self.assertEqual("ENDED", ended["document"]["session"]["state"])
        history = self.repository.read_history(self.session_id)
        self.assertEqual(5, history["session_document"]["positions"][0]["holding_qty"])
        self.assertEqual("", self.repository.current_session_id("005930"))

        session_v2 = "MV-00000000000000000000000000000002"
        created_v2 = self.create(session_v2)["document"]
        self.assertEqual(session_v2, created_v2["session"]["validation_session_id"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in created_v2["positions"]))
        self.assertEqual(5, self.repository.read_history(self.session_id)["session_document"]["positions"][0]["holding_qty"])

    def test_ended_session_duplicate_never_becomes_current_again(self) -> None:
        self.start()
        self.service.end_stock_session(self.session_id, command_id="MC-end-immutable")
        duplicate = self.create()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("ENDED", duplicate["document"]["session"]["state"])
        self.assertEqual("", self.repository.current_session_id("A005930"))

    def test_order_fill_contract_updates_only_original_order(self) -> None:
        self.start()
        order_a = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=3, requested_price=100, command_id="MC-order-a",
        )["order"]
        order_b = self.service.create_order(
            self.session_id, routine_instance_id="B", side="BUY", order_type="LIMIT",
            requested_qty=2, requested_price=100, command_id="MC-order-b",
        )["order"]
        self.service.transition_order(self.session_id, order_a["mock_order_id"], "OPEN", command_id="MC-open-a")
        self.service.append_fill(
            self.session_id, mock_order_id=order_a["mock_order_id"], qty=1, price=100,
            market_snapshot_identity="BOOK-1", command_id="MC-fill-a1",
        )
        document = self.repository.read_session(self.session_id)
        orders = {item["mock_order_id"]: item for item in document["orders"]}
        self.assertEqual("PARTIAL_FILL", orders[order_a["mock_order_id"]]["state"])
        self.assertEqual(2, orders[order_a["mock_order_id"]]["remaining_qty"])
        self.assertEqual("CREATED", orders[order_b["mock_order_id"]]["state"])
        self.assertEqual("A", document["fills"][0]["routine_instance_id"])

    def test_same_command_is_idempotent(self) -> None:
        self.start()
        first = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=1, command_id="MC-same",
        )
        second = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=1, command_id="MC-same",
        )
        self.assertTrue(first["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(1, len(self.repository.read_session(self.session_id)["orders"]))

    def test_writer_rejects_production_and_outside_paths(self) -> None:
        for forbidden in ("runtime", "stocks", "routine_instances", "performance_ledger"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(MockValidationError, "MOCK_ROOT_OVERLAPS_PRODUCTION"):
                    MockValidationRepository(PROJECT_ROOT / forbidden)
        with self.assertRaisesRegex(MockValidationError, "MOCK_WRITE_PATH_OUTSIDE_ROOT"):
            self.repository.read_object(PROJECT_ROOT / "runtime" / "order_queue.json")

    def test_malformed_session_fails_closed(self) -> None:
        self.create()
        with self.assertRaisesRegex(MockValidationError, "MOCK_SESSION_SCHEMA_INVALID"):
            self.repository.mutate_session(
                self.session_id,
                lambda document: {**document, "schema_version": "broken"},
            )

    def test_invalid_position_and_market_order_price_fail_closed(self) -> None:
        self.start()
        with self.assertRaisesRegex(MockValidationError, "MOCK_POSITION_AVAILABLE_EXCEEDS_HOLDING"):
            self.service.set_instance_position(
                self.session_id, "A", holding_qty=1, available_qty=2,
                average_price=10, realized_cost_basis=10, command_id="MC-invalid-position",
            )
        with self.assertRaisesRegex(MockValidationError, "MOCK_MARKET_ORDER_PRICE_MUST_BE_EMPTY"):
            self.service.create_order(
                self.session_id, routine_instance_id="A", side="BUY", order_type="MARKET",
                requested_qty=1, requested_price=10, command_id="MC-invalid-market",
            )

    def test_created_or_terminal_order_cannot_receive_fill(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=10, command_id="MC-created-order",
        )["order"]
        with self.assertRaisesRegex(MockValidationError, "MOCK_ORDER_NOT_FILLABLE"):
            self.service.append_fill(
                self.session_id, mock_order_id=order["mock_order_id"], qty=1, price=10,
                market_snapshot_identity="BOOK-X", command_id="MC-created-fill",
            )

    def test_pnl_totals_fail_closed_when_inconsistent(self) -> None:
        self.start()
        with self.assertRaisesRegex(MockValidationError, "MOCK_PNL_GROSS_MISMATCH"):
            self.service.set_instance_pnl(
                self.session_id, "A", realized_pnl=10, unrealized_pnl=5,
                gross_pnl=99, commission=1, mock_tax=2, net_pnl=96,
                command_id="MC-invalid-pnl",
            )

    def test_foundation_event_command_is_idempotent(self) -> None:
        self.create()
        first = self.service.start_stock_mock_session(self.session_id, command_id="MC-repeat-start")
        second = self.service.start_stock_mock_session(self.session_id, command_id="MC-repeat-start")
        self.assertTrue(first["started"])
        self.assertTrue(second["duplicate"])
        events = [item for item in self.repository.read_events(self.session_id) if item["event_type"] == "SESSION_STARTED"]
        self.assertEqual(1, len(events))

    def test_mock_operations_do_not_change_production_mutables(self) -> None:
        production_roots = tuple(
            PROJECT_ROOT / name
            for name in ("runtime", "stocks", "routine_instances", "performance_ledger")
        )
        protected_files = (PROJECT_ROOT / "operation_policy.json",)
        before_trees = _tree_hashes(*production_roots)
        before_files = {str(path): _file_hash(path) for path in protected_files}
        self.create()
        self.service.set_instance_effective_settings(
            self.session_id,
            routine_instance_id="A",
            initial_buy={"mode": "AMOUNT", "value": 500_000},
            operation_schedule={
                "start_time": "10:30:00",
                "end_buy_time": "13:30:00",
            },
            operation_mode="MANUAL_ATS",
            command_id="MC-settings-isolation",
        )
        self.service.start_stock_mock_session(
            self.session_id,
            command_id="MC-start-isolation",
        )
        self.service.stop_for_instance_error(
            self.session_id, source_routine_instance_id="C",
            reason_code="ISOLATION", reason="test", command_id="MC-isolation",
        )
        self.service.reset_stock_session(self.session_id, command_id="MC-reset-isolation")
        after_trees = _tree_hashes(*production_roots)
        after_files = {str(path): _file_hash(path) for path in protected_files}
        self.assertEqual(before_trees, after_trees)
        self.assertEqual(before_files, after_files)


if __name__ == "__main__":
    unittest.main()
