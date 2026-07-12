from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

import rule_approval_session_file_service as service


def _load_mapper_module():
    project_root = Path(__file__).resolve().parents[1]
    mapper_path = next((project_root / "routines").glob("*/routine_rule_mapper.py"))
    spec = spec_from_file_location("routine_rule_mapper_session_file_test", mapper_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RuleApprovalSessionFileServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = _load_mapper_module()
        self.current_rules = {
            "buy": {
                "groups": [
                    {
                        "conditions": [
                            {
                                "target": "OSC",
                                "operator": "TURN_UP",
                            }
                        ]
                    }
                ]
            },
            "sell": {
                "signals": {
                    "macd_sell": {
                        "enabled": True,
                    }
                }
            },
        }
        self.preview_result = {
            "preview_rules": {
                "indicator_follow_rule_preview": {
                    "mode": "merge_add_candidate",
                    "candidates": {
                        "buy": {
                            "merge_into": "buy.groups[0].conditions",
                            "skip_existing": [
                                {
                                    "target": "OSC",
                                    "operator": "TURN_UP",
                                }
                            ],
                            "add_conditions": [
                                {
                                    "target": "OSC",
                                    "operator": "<=",
                                    "value": -91.0,
                                }
                            ],
                        },
                        "sell": {
                            "add_signal_candidate": {
                                "path": "sell.signals.ui_preview_condition_c",
                                "enabled": False,
                                "preview_candidate": True,
                                "groups": [
                                    {
                                        "conditions": [
                                            {
                                                "target": "MACD",
                                                "operator": "<=",
                                                "value": -1.0,
                                            }
                                        ],
                                    }
                                ],
                            }
                        },
                    },
                }
            },
            "mapped_paths": [
                "buy.groups[0].conditions",
                "sell.signals.ui_preview_condition_c",
            ],
            "warnings": [],
        }

    def _rules_json_hash(self) -> str:
        project_root = Path(__file__).resolve().parents[1]
        rules_path = next((project_root / "routines").glob("*/rules.json"))
        return hashlib.sha256(rules_path.read_bytes()).hexdigest().upper()

    def _runtime_file_snapshot(self) -> dict[str, tuple[int, float]]:
        project_root = Path(__file__).resolve().parents[1]
        runtime_path = project_root / "runtime"
        return {
            path.name: (path.stat().st_size, path.stat().st_mtime)
            for path in runtime_path.glob("*")
            if path.is_file()
        }

    def _session(self) -> dict[str, object]:
        session = self.mapper.build_rule_approval_session(
            self.preview_result,
            {
                "buy.groups[0].conditions": "APPROVED",
                "sell.signals.ui_preview_condition_c": "DEFERRED",
            },
        )
        fingerprint = self.mapper.build_rule_approval_session_fingerprint(
            self.current_rules,
            self.preview_result,
        )
        session["fingerprint"] = fingerprint["fingerprint"]
        session["fingerprint_detail"] = fingerprint
        return session

    def test_save_success_writes_minimal_session_to_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "runtime" / "routines" / "indicator_follow" / "approval_session.json"

            result = service.save_rule_approval_session(self._session(), session_path)
            data = json.loads(session_path.read_text(encoding="utf-8"))

            self.assertTrue(result["ok"])
            self.assertTrue(result["saved"])
            self.assertEqual(data["mode"], "indicator_follow_rule_approval_session")
            self.assertEqual(data["routine"], "지표추종매매")
            self.assertEqual(data["routine_key"], "indicator_follow")
            self.assertEqual(data["decisions"]["buy.groups[0].conditions"], "APPROVED")
            self.assertNotIn("patch_preview", data)
            self.assertNotIn("apply_preview", data)
            self.assertNotIn("fingerprint_detail", data)

    def test_load_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            service.save_rule_approval_session(self._session(), session_path)

            result = service.load_rule_approval_session(session_path)

            self.assertTrue(result["ok"])
            self.assertTrue(result["exists"])
            self.assertEqual(result["session"]["decisions"]["buy.groups[0].conditions"], "APPROVED")

    def test_load_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = service.load_rule_approval_session(Path(temp_dir) / "missing.json")

            self.assertTrue(result["ok"])
            self.assertFalse(result["exists"])
            self.assertIsNone(result["session"])

    def test_load_corrupted_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            session_path.write_text("{bad", encoding="utf-8")

            result = service.load_rule_approval_session(session_path)

            self.assertFalse(result["ok"])
            self.assertEqual(result["stage"], "read_session")

    def test_load_root_non_dict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"
            session_path.write_text("[]", encoding="utf-8")

            result = service.load_rule_approval_session(session_path)

            self.assertFalse(result["ok"])
            self.assertEqual(result["stage"], "session_structure")

    def test_restore_unknown_decision_is_safely_pending(self) -> None:
        saved = self._session()
        saved["decisions"]["buy.groups[0].conditions"] = "UNKNOWN"

        result = service.restore_saved_rule_approval_session(
            saved,
            self.current_rules,
            self.preview_result,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["restore_status"], "RESTORED")
        self.assertEqual(result["session"]["decisions"]["buy.groups[0].conditions"], "PENDING")
        self.assertIn(
            "unknown approval decision ignored for buy.groups[0].conditions: UNKNOWN",
            result["warnings"],
        )

    def test_restore_fingerprint_mismatch_resets_to_pending(self) -> None:
        saved = self._session()
        saved["fingerprint"] = "stale"

        result = service.restore_saved_rule_approval_session(
            saved,
            self.current_rules,
            self.preview_result,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["restore_status"], "RESET_TO_PENDING")
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})

    def test_restore_path_mismatch_resets_to_pending(self) -> None:
        saved = self._session()
        saved["decisions"].pop("sell.signals.ui_preview_condition_c")

        result = service.restore_saved_rule_approval_session(
            saved,
            self.current_rules,
            self.preview_result,
        )

        self.assertEqual(result["restore_status"], "RESET_TO_PENDING")
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})

    def test_restore_type_mismatch_resets_to_pending(self) -> None:
        saved = self._session()
        saved["candidate_types"]["buy.groups[0].conditions"] = "add_signal"

        result = service.restore_saved_rule_approval_session(
            saved,
            self.current_rules,
            self.preview_result,
        )

        self.assertEqual(result["restore_status"], "RESET_TO_PENDING")
        self.assertEqual(set(result["session"]["decisions"].values()), {"PENDING"})

    def test_save_blocks_unknown_decision(self) -> None:
        session = self._session()
        session["decisions"]["buy.groups[0].conditions"] = "UNKNOWN"
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"

            result = service.save_rule_approval_session(session, session_path)

            self.assertFalse(result["ok"])
            self.assertFalse(session_path.exists())

    def test_inputs_are_not_mutated(self) -> None:
        session = self._session()
        original_session = deepcopy(session)
        original_rules = deepcopy(self.current_rules)
        original_preview = deepcopy(self.preview_result)
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"

            service.save_rule_approval_session(session, session_path)
            load_result = service.load_rule_approval_session(session_path)
            service.restore_saved_rule_approval_session(
                load_result["session"],
                self.current_rules,
                self.preview_result,
            )

        self.assertEqual(session, original_session)
        self.assertEqual(self.current_rules, original_rules)
        self.assertEqual(self.preview_result, original_preview)

    def test_rules_json_is_not_written(self) -> None:
        before = self._rules_json_hash()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "approval_session.json"

            service.save_rule_approval_session(self._session(), session_path)
            loaded = service.load_rule_approval_session(session_path)
            service.restore_saved_rule_approval_session(
                loaded["session"],
                self.current_rules,
                self.preview_result,
            )

        self.assertEqual(before, self._rules_json_hash())

    def test_runtime_write_is_limited_to_explicit_session_path(self) -> None:
        before_runtime = self._runtime_file_snapshot()
        with tempfile.TemporaryDirectory() as temp_dir:
            session_path = Path(temp_dir) / "runtime" / "routines" / "indicator_follow" / "approval_session.json"

            service.save_rule_approval_session(self._session(), session_path)

            self.assertTrue(session_path.exists())
            self.assertEqual(list(session_path.parent.glob("*.tmp")), [])
            self.assertEqual(list(session_path.parent.glob(".*.tmp")), [])
        self.assertEqual(before_runtime, self._runtime_file_snapshot())


if __name__ == "__main__":
    unittest.main()

