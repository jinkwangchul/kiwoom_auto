from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from operator_reconciliation_service import assess_startup_recovery


class RuntimeRecoveryE2ETest(unittest.TestCase):
    _RUNTIME_FIELDS = {
        "queue_path": ("order_queue.json", "orders"),
        "fills_path": ("fills.json", "fills"),
        "positions_path": ("positions.json", "positions"),
        "broker_holdings_path": ("broker_holdings.json", "holdings"),
        "order_executions_path": ("order_executions.json", "executions"),
        "order_locks_path": ("order_locks.json", "locks"),
        "routine_signals_path": ("routine_signals.json", "signals"),
    }

    def _paths(self, root: Path, *, create: bool = True) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for argument, (filename, field) in self._RUNTIME_FIELDS.items():
            path = root / filename
            paths[argument] = path
            if create:
                path.write_text(
                    json.dumps(
                        {"version": 1, "revision": 0, "updated_at": "before", field: []},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
        return paths

    def test_all_runtime_files_missing_is_invalid_read_only_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root, create=False)

            result = assess_startup_recovery(**paths)

            self.assertEqual("INVALID_RUNTIME", result["status"])
            self.assertFalse(result["operator_approval_allowed"])
            self.assertFalse(result["automatic_trading_allowed"])
            self.assertEqual(set(map(str, paths.values())), set(result["missing_files"]))
            self.assertTrue(all(not path.exists() for path in paths.values()))
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["queue_write"])
            self.assertFalse(result["file_write"])

    def test_missing_required_stock_status_is_invalid_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            state_path = root / "state.json"
            original = json.dumps({"trade_enabled": False}, ensure_ascii=False).encode("utf-8")
            state_path.write_bytes(original)

            result = assess_startup_recovery(
                **paths,
                stock_state_paths=[state_path],
            )

            self.assertEqual("INVALID_RUNTIME", result["status"])
            self.assertFalse(result["operator_approval_allowed"])
            self.assertIn("required field status is missing", " ".join(result["invalid_reasons"]))
            self.assertEqual(original, state_path.read_bytes())

    def test_damaged_json_and_partial_runtime_remain_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            damaged = paths["positions_path"]
            missing = paths["order_locks_path"]
            damaged.write_text("{broken", encoding="utf-8")
            missing.unlink()
            before = {
                path: path.read_bytes()
                for path in paths.values()
                if path.exists()
            }

            result = assess_startup_recovery(**paths)

            self.assertEqual("INVALID_RUNTIME", result["status"])
            self.assertFalse(result["operator_approval_allowed"])
            self.assertIn("positions.json", " ".join(result["invalid_reasons"]))
            self.assertIn("order_locks.json", " ".join(result["invalid_reasons"]))
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertFalse(missing.exists())

    def test_restart_reassessment_restores_identical_runtime_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = self._paths(root)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}, ensure_ascii=False),
                encoding="utf-8",
            )
            before = {path: path.read_bytes() for path in [*paths.values(), state_path]}

            first = assess_startup_recovery(**paths, stock_state_paths=[state_path])
            second = assess_startup_recovery(**paths, stock_state_paths=[state_path])

            self.assertEqual("RESUME_READY", first["status"])
            self.assertEqual(first["status"], second["status"])
            self.assertEqual(first["snapshot_hash"], second["snapshot_hash"])
            self.assertEqual(before, {path: path.read_bytes() for path in before})


if __name__ == "__main__":
    unittest.main()
