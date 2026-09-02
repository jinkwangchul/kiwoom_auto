from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import order_candidate_engine
import routine_signal_probe
from routine_instance_registry import load_routine_definitions
from routine_package_contract import (
    EVALUATION_ROLE,
    EXECUTION_ADMISSION_ROLE,
    FINAL_SAFETY_ROLE,
    RULE_MAPPER_ROLE,
    SETTINGS_ROLE,
    evaluate_routine_gate,
    load_routine_callable,
    load_routine_module,
    validate_routine_definition_capabilities,
)


class RoutinePackageContractTest(unittest.TestCase):
    def _write_package(self, root: Path, definition_id: str, *, side: str) -> str:
        package = root / "routines" / definition_id
        package.mkdir(parents=True)
        evaluation_file = f"{definition_id}_entry.py"
        metadata = {
            "schema_version": "1.0",
            "definition_id": definition_id,
            "name": f"Dummy {definition_id}",
            "entry_file": evaluation_file,
            "rules_file": "rules.json",
            "locators": {
                "evaluation": {"file": evaluation_file, "callable": "evaluate"},
                "settings": {"file": "settings.py", "callable": "SettingsDialog"},
                "rule_mapper": {"file": "mapper.py"},
                "execution_admission": {
                    "file": evaluation_file,
                    "callable": "admit",
                },
                "final_safety": {
                    "file": evaluation_file,
                    "callable": "final_safety",
                },
            },
        }
        (package / "routine.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        (package / evaluation_file).write_text(
            f"""
def evaluate(context):
    return {{'signal': '{side}', 'definition': '{definition_id}'}}

def _result(allowed, routine_identity, rules_identity, reason=''):
    return {{
        'allowed': bool(allowed),
        'reason': reason,
        'routine_identity': routine_identity,
        'rules_identity': rules_identity,
    }}

def admit(subject, rules, routine_identity, rules_identity):
    return _result(rules.get('admit') is True, routine_identity, rules_identity, 'ADMISSION')

def final_safety(subject, rules, routine_identity, rules_identity):
    if rules.get('raise_final'):
        raise RuntimeError('final failed')
    return _result(rules.get('safe') is True, routine_identity, rules_identity, 'FINAL')
""",
            encoding="utf-8",
        )
        (package / "settings.py").write_text(
            "class SettingsDialog:\n    pass\n", encoding="utf-8"
        )
        (package / "mapper.py").write_text("MAPPER_ID = '" + definition_id + "'\n", encoding="utf-8")
        (package / "rules.json").write_text("{}\n", encoding="utf-8")

        instance_id = {
            "dummy_a": "11111111-1111-4111-8111-111111111111",
            "dummy_b": "22222222-2222-4222-8222-222222222222",
            "dummy_c": "33333333-3333-4333-8333-333333333333",
        }[definition_id]
        instance = root / "routine_instances" / instance_id
        instance.mkdir(parents=True)
        (instance / "instance.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "instance_id": instance_id,
                    "definition_id": definition_id,
                    "display_name": f"{definition_id} instance",
                    "enabled": True,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                }
            ),
            encoding="utf-8",
        )
        (instance / "rules.json").write_text(
            json.dumps(
                {
                    "admit": definition_id == "dummy_a",
                    "safe": definition_id == "dummy_a",
                }
            ),
            encoding="utf-8",
        )
        return instance_id

    def test_dummy_a_and_b_resolve_every_generic_capability_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a_id = self._write_package(root, "dummy_a", side="BUY")
            b_id = self._write_package(root, "dummy_b", side="SELL")
            self._write_package(root, "dummy_c", side="NONE")
            definitions = {
                item.definition_id: item
                for item in load_routine_definitions(project_root=root)
            }

            for definition_id in ("dummy_a", "dummy_b", "dummy_c"):
                definition = definitions[definition_id]
                validation = validate_routine_definition_capabilities(definition)
                self.assertTrue(validation["ok"], validation)
                self.assertTrue(callable(load_routine_callable(definition, SETTINGS_ROLE)))
                self.assertEqual(
                    definition_id,
                    load_routine_module(definition, RULE_MAPPER_ROLE).MAPPER_ID,
                )
                evaluation = load_routine_callable(definition, EVALUATION_ROLE)
                self.assertEqual(
                    {"dummy_a": "BUY", "dummy_b": "SELL", "dummy_c": "NONE"}[
                        definition_id
                    ],
                    evaluation({})["signal"],
                )
                self.assertEqual(
                    evaluation({}),
                    routine_signal_probe._load_routine_module(definition).evaluate({}),
                )

            admitted_a = evaluate_routine_gate(
                instance_id=a_id,
                role=EXECUTION_ADMISSION_ROLE,
                subject={"stock_code": "005930"},
                project_root=root,
            )
            admitted_b = evaluate_routine_gate(
                instance_id=b_id,
                role=EXECUTION_ADMISSION_ROLE,
                subject={"stock_code": "000660"},
                project_root=root,
            )
            safe_a = evaluate_routine_gate(
                instance_id=a_id,
                role=FINAL_SAFETY_ROLE,
                subject={"stock_code": "005930"},
                project_root=root,
            )

        self.assertTrue(admitted_a["allowed"])
        self.assertFalse(admitted_b["allowed"])
        self.assertTrue(safe_a["allowed"])
        self.assertNotEqual(
            admitted_a["routine_identity"], admitted_b["routine_identity"]
        )

    def test_gate_exception_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance_id = self._write_package(root, "dummy_a", side="BUY")
            rules_path = root / "routine_instances" / instance_id / "rules.json"
            rules_path.write_text(
                json.dumps({"admit": True, "safe": True, "raise_final": True}),
                encoding="utf-8",
            )

            result = evaluate_routine_gate(
                instance_id=instance_id,
                role=FINAL_SAFETY_ROLE,
                subject={},
                project_root=root,
            )

        self.assertFalse(result["allowed"])
        self.assertEqual("ROUTINE_GATE_UNAVAILABLE", result["reason"])

    def test_common_candidate_accepts_buy_and_sell_without_strategy_dispatch(self) -> None:
        base = {
            "quantity": 1,
            "budget": 1000,
            "price_basis": "ORDER_PRICE",
            "price": 1000,
            "hoga": "LIMIT",
            "routine_instance_id": "instance",
            "source_signal_id": "signal",
            "routine_provenance": {"opaque": "value"},
        }
        buy = order_candidate_engine.build_order_candidate_from_execution_intent(
            {**base, "side": "BUY"}
        )
        sell = order_candidate_engine.build_order_candidate_from_execution_intent(
            {**base, "side": "SELL", "source_signal_id": "sell-signal"}
        )

        self.assertEqual("CANDIDATE_READY", buy["candidate_status"])
        self.assertEqual("CANDIDATE_READY", sell["candidate_status"])
        self.assertEqual("BUY_SIGNAL_CANDIDATE", buy["order_type"])
        self.assertEqual("SELL_SIGNAL_CANDIDATE", sell["order_type"])
        self.assertEqual({"opaque": "value"}, sell["order_intent"]["routine_provenance"])

    def test_common_boundary_files_have_no_indicator_follow_dispatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        common_files = (
            "routine_signal_probe.py",
            "routine_signal_consumer.py",
            "order_candidate_engine.py",
            "order_queue.py",
            "auto_trade_order_execution_boundary.py",
            "rule_apply_commit_service.py",
            "rule_commit_dry_run_service.py",
        )
        forbidden = (
            "INDICATOR_FOLLOW",
            "IndicatorFollowRoutineSettingsDialog",
            "principle.execution_enabled",
            "safety.real_order_allowed",
            "buy.execution",
            "macd_sell",
        )
        for file_name in common_files:
            source = (root / file_name).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, f"{file_name}: {token}")


if __name__ == "__main__":
    unittest.main()
