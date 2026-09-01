import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from gui_auto_trade_integrity import (
    REVIEW_INSPECTION_STATE_DATA_INCONSISTENT,
    REVIEW_INSPECTION_STATE_MISSING,
    REVIEW_INSPECTION_STATE_READ_ERROR,
    inspect_review_state_path,
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_protected_stock_dir,
    is_review_required_stock_dir,
    is_review_required_state,
)


class AutoTradeStatePredicateTests(unittest.TestCase):
    def _stock_dir(self, root: str, state: dict[str, object]) -> Path:
        stock_dir = Path(root) / "000001_TEST"
        stock_dir.mkdir(parents=True, exist_ok=True)
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "orders.json").write_text("[]", encoding="utf-8")
        return stock_dir

    def test_review_required_state_accepts_canonical_and_compatibility_inputs(self):
        self.assertTrue(is_review_required_state({"status": "REVIEW_REQUIRED"}))
        self.assertTrue(is_review_required_state({"status": "REVIEW"}))
        self.assertTrue(is_review_required_state({"review_required": True}))
        self.assertTrue(is_review_required_state({"review_required": "true"}))
        self.assertTrue(is_review_required_state({"review_status": "PENDING"}))
        self.assertTrue(is_review_required_state({"review_status": "검토필요"}))

    def test_review_required_state_rejects_normal_and_invalid_inputs(self):
        self.assertFalse(is_review_required_state({"status": "MONITORING"}))
        self.assertFalse(is_review_required_state({}))
        self.assertFalse(is_review_required_state(None))
        self.assertFalse(is_review_required_state(["REVIEW_REQUIRED"]))

    def test_emergency_stopped_state_accepts_canonical_and_legacy_inputs(self):
        self.assertTrue(is_emergency_stopped_state({"status": "EMERGENCY_STOPPED"}))
        self.assertTrue(is_emergency_stopped_state({"status": "EMERGENCY_STOP"}))
        self.assertTrue(is_emergency_stopped_state({"status": "EMERGENCY"}))

    def test_emergency_stopped_state_rejects_normal_and_invalid_inputs(self):
        self.assertFalse(is_emergency_stopped_state({"status": "STOPPED"}))
        self.assertFalse(is_emergency_stopped_state({}))
        self.assertFalse(is_emergency_stopped_state(None))
        self.assertFalse(is_emergency_stopped_state("EMERGENCY_STOPPED"))

    def test_operation_excluded_reads_config_flag_only(self):
        self.assertTrue(is_operation_excluded({"operation_excluded": True}))
        self.assertTrue(is_operation_excluded({"operation_excluded": "true"}))
        self.assertTrue(is_operation_excluded({"operation_excluded": "1"}))
        self.assertFalse(is_operation_excluded({"operation_excluded": False}))
        self.assertFalse(is_operation_excluded({"operation_excluded": "false"}))
        self.assertFalse(is_operation_excluded({}))
        self.assertFalse(is_operation_excluded(None))

    def test_combined_state_helpers_do_not_override_each_other(self):
        state = {"status": "EMERGENCY_STOPPED", "review_required": True}
        config = {"operation_excluded": True}

        self.assertTrue(is_review_required_state(state))
        self.assertTrue(is_emergency_stopped_state(state))
        self.assertTrue(is_operation_excluded(config))

    def test_review_protected_stock_dir_accepts_review_state(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock_dir(root, {"status": "REVIEW_REQUIRED"})

            self.assertTrue(is_review_protected_stock_dir(stock_dir))

    def test_review_protected_stock_dir_rejects_normal_state(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock_dir(root, {"status": "STOPPED"})

            self.assertFalse(is_review_protected_stock_dir(stock_dir))

    def test_review_protected_stock_dir_protects_missing_or_corrupt_state(self):
        with TemporaryDirectory() as root:
            stock_dir = Path(root) / "000001_TEST"
            stock_dir.mkdir(parents=True)
            self.assertTrue(is_review_protected_stock_dir(stock_dir))

            (stock_dir / "state.json").write_text("{broken", encoding="utf-8")
            self.assertTrue(is_review_protected_stock_dir(stock_dir))

    def test_review_inspector_fails_closed_for_missing_and_corrupt_state(self):
        with TemporaryDirectory() as root:
            stock_dir = Path(root) / "000001_TEST"
            stock_dir.mkdir(parents=True)
            state_path = stock_dir / "state.json"

            missing = inspect_review_state_path(state_path)
            self.assertTrue(missing.review_required)
            self.assertFalse(missing.state_valid)
            self.assertEqual(REVIEW_INSPECTION_STATE_MISSING, missing.reason_code)
            self.assertTrue(is_review_required_stock_dir(stock_dir))

            state_path.write_text("{broken", encoding="utf-8")
            corrupt = inspect_review_state_path(state_path)
            self.assertTrue(corrupt.review_required)
            self.assertFalse(corrupt.state_valid)
            self.assertEqual(REVIEW_INSPECTION_STATE_READ_ERROR, corrupt.reason_code)
            self.assertTrue(is_review_required_stock_dir(stock_dir))

            state_path.write_text(
                json.dumps({"status": "STOPPED", "holding_qty": "invalid"}),
                encoding="utf-8",
            )
            partial = inspect_review_state_path(state_path)
            self.assertTrue(partial.review_required)
            self.assertFalse(partial.state_valid)
            self.assertEqual(
                REVIEW_INSPECTION_STATE_DATA_INCONSISTENT,
                partial.reason_code,
            )
            self.assertIn("holding_qty 숫자 형식 오류", partial.issues)

    def test_emergency_stopped_state_normalizes_case_and_whitespace(self):
        self.assertTrue(is_emergency_stopped_state({"status": " emergency_stopped "}))

    def test_operation_excluded_ignores_unrelated_config_flags(self):
        self.assertFalse(is_operation_excluded({"trade_enabled": False, "excluded": True}))

    def test_review_protected_stock_dir_accepts_compatibility_review_flag(self):
        with TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {"status": "MONITORING", "review_required": "true"},
            )

            self.assertTrue(is_review_protected_stock_dir(stock_dir))


if __name__ == "__main__":
    unittest.main()
