from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from startup_runtime_residue_disposal_service import (
    CLASSIFICATION_DEVELOPMENT_RESIDUE,
    CLASSIFICATION_OPERATION_EVIDENCE,
    dispose_confirmed_startup_runtime_residue,
    inspect_startup_runtime_residue,
)


class StartupRuntimeResidueDisposalServiceTest(unittest.TestCase):
    def _write_residue(self, runtime_dir: Path) -> None:
        runtime_dir.mkdir()
        (runtime_dir / "order_queue.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "revision": 1,
                    "orders": [
                        {
                            "id": "TEST_ORDER",
                            "status": "BLOCKED",
                            "quantity": 0,
                            "execution_enabled": False,
                            "tick_key": "manual_order_queue_candidate_test",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (runtime_dir / "routine_signals.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "signals": [
                        {
                            "id": "TEST_SIGNAL",
                            "status": "BLOCKED",
                            "execution_enabled": False,
                            "source": "manual_verification",
                            "preview_summary": {"send_order_called": False},
                            "order_manager_result": {"order_executor_called": False},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_confirmed_residue_is_archived_and_initialized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime_dir = root / "runtime"
            archive_dir = root / "archive"
            self._write_residue(runtime_dir)
            inspection = inspect_startup_runtime_residue(runtime_dir)

            result = dispose_confirmed_startup_runtime_residue(
                runtime_dir,
                archive_dir,
                confirmed_inspection=inspection,
                manual_disposal_confirmed=True,
            )

            self.assertEqual(
                CLASSIFICATION_DEVELOPMENT_RESIDUE,
                inspection["classification"],
            )
            self.assertEqual("DISPOSED_AND_INITIALIZED", result["status"])
            self.assertTrue((archive_dir / "manifest.json").exists())
            self.assertEqual(7, len(list(runtime_dir.glob("*.json"))))

    def test_execution_evidence_is_never_disposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime_dir = root / "runtime"
            self._write_residue(runtime_dir)
            queue_path = runtime_dir / "order_queue.json"
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            queue["orders"][0]["status"] = "BROKER_ACCEPTED"
            queue["orders"][0]["broker_order_no"] = "1234"
            queue_path.write_text(json.dumps(queue), encoding="utf-8")

            inspection = inspect_startup_runtime_residue(runtime_dir)
            result = dispose_confirmed_startup_runtime_residue(
                runtime_dir,
                root / "archive",
                confirmed_inspection=inspection,
                manual_disposal_confirmed=True,
            )

            self.assertEqual(
                CLASSIFICATION_OPERATION_EVIDENCE,
                inspection["classification"],
            )
            self.assertEqual("BLOCKED", result["status"])
            self.assertTrue(queue_path.exists())


if __name__ == "__main__":
    unittest.main()
