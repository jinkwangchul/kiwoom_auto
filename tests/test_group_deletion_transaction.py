from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import group_deletion_transaction as transaction


class GroupDeletionTransactionTests(unittest.TestCase):
    def test_uuid_marker_round_trip_uses_transaction_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_id = "11111111-1111-4111-8111-111111111111"

            marked = transaction.mark_group_delete_pending(root, group_id)
            repeated = transaction.mark_group_delete_pending(root, group_id)
            payload = json.loads(marked.marker_path.read_text(encoding="utf-8"))

            self.assertTrue(marked.success)
            self.assertTrue(marked.changed)
            self.assertFalse(repeated.changed)
            self.assertEqual(
                root / "groups" / ".transactions" / f"{group_id}.delete.json",
                marked.marker_path,
            )
            self.assertEqual(group_id, payload["group_id"])
            self.assertEqual("DELETE", payload["operation"])
            self.assertEqual("PENDING", payload["state"])
            self.assertTrue(transaction.group_delete_pending(root, group_id))

            cleared = transaction.clear_group_delete_pending(root, group_id)

            self.assertTrue(cleared.success)
            self.assertFalse(transaction.group_delete_pending(root, group_id))

    def test_non_uuid_group_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_id = str((root / "_Legacy").resolve())

            marked = transaction.mark_group_delete_pending(root, group_id)

        self.assertFalse(marked.success)
        self.assertFalse(transaction.group_delete_pending(root, group_id))

    def test_failed_promotion_leaves_no_pending_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_id = "11111111-1111-4111-8111-111111111111"
            with patch.object(transaction.os, "replace", side_effect=OSError("blocked")):
                result = transaction.mark_group_delete_pending(root, group_id)

            self.assertFalse(result.success)
            self.assertFalse(transaction.group_delete_pending(root, group_id))
            self.assertEqual([], list((root / "groups" / ".transactions").glob("*.tmp")))

    def test_corrupt_existing_marker_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group_id = "11111111-1111-4111-8111-111111111111"
            marker_path = transaction.group_delete_marker_path(root, group_id)
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text("{}\n", encoding="utf-8")

            result = transaction.mark_group_delete_pending(root, group_id)
            cleared = transaction.clear_group_delete_pending(root, group_id)

            self.assertFalse(result.success)
            self.assertFalse(cleared.success)
            self.assertTrue(transaction.group_delete_pending(root, group_id))
            self.assertEqual("{}\n", marker_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
