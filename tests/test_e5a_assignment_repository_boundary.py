from __future__ import annotations

from pathlib import Path
import unittest

import assignment_episode_linkage
import stock_repository


class AssignmentRepositoryBoundaryTests(unittest.TestCase):
    def test_repository_exposes_no_assignment_transaction_methods(self) -> None:
        for method_name in (
            "update_stock_routine",
            "update_stock_routine_result",
            "update_stock_routine_instance",
            "update_stock_routine_instance_result",
        ):
            self.assertFalse(hasattr(stock_repository.StockRepository, method_name))

    def test_repository_has_no_assignment_application_dependency(self) -> None:
        source = Path(stock_repository.__file__).read_text(encoding="utf-8")
        self.assertNotIn("assignment_episode_linkage", source)

    def test_application_owner_exposes_assign_and_unassign_entries(self) -> None:
        self.assertTrue(callable(assignment_episode_linkage.assign_stock_routine))
        self.assertTrue(callable(assignment_episode_linkage.unassign_stock_routine))


if __name__ == "__main__":
    unittest.main()
