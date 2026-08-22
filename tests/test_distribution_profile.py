from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from distribution_profile import (
    DISTRIBUTION_PROFILE_ENV,
    distribution_profile,
    group_packing_enabled,
)


class DistributionProfileTest(unittest.TestCase):
    def test_developer_enables_group_packing(self) -> None:
        with patch.dict(os.environ, {DISTRIBUTION_PROFILE_ENV: "developer"}, clear=False):
            self.assertEqual("developer", distribution_profile())
            self.assertTrue(group_packing_enabled())

    def test_beta_and_production_disable_group_packing(self) -> None:
        for value in ("beta", "production"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {DISTRIBUTION_PROFILE_ENV: value},
                clear=False,
            ):
                self.assertEqual(value, distribution_profile())
                self.assertFalse(group_packing_enabled())

    def test_missing_profile_fails_closed_to_production(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(DISTRIBUTION_PROFILE_ENV, None)
            self.assertEqual("production", distribution_profile())
            self.assertFalse(group_packing_enabled())

    def test_invalid_profile_fails_closed_to_production(self) -> None:
        with patch.dict(os.environ, {DISTRIBUTION_PROFILE_ENV: "preview"}, clear=False):
            self.assertEqual("production", distribution_profile())
            self.assertFalse(group_packing_enabled())


if __name__ == "__main__":
    unittest.main()
