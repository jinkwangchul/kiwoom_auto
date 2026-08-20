from __future__ import annotations

import unittest

from kiwoom_screen_allocator import (
    ACCOUNT_TR,
    ADMIN_GUI,
    CONDITION,
    MARKET_TR,
    ORDER,
    REALTIME,
    SCREEN_ALREADY_LEASED,
    SCREEN_OUT_OF_RANGE,
    SCREEN_POOL_EXHAUSTED,
    KiwoomScreenAllocator,
    ScreenAllocationError,
    load_project_screen_ranges,
    project_order_default_screen_no,
)


class KiwoomScreenAllocatorTests(unittest.TestCase):
    def test_registry_namespace_matches_project_ranges(self) -> None:
        ranges = load_project_screen_ranges()

        self.assertEqual((1000, 1999), (ranges[ACCOUNT_TR].start, ranges[ACCOUNT_TR].end))
        self.assertEqual((2000, 2999), (ranges[CONDITION].start, ranges[CONDITION].end))
        self.assertEqual((3000, 3999), (ranges[MARKET_TR].start, ranges[MARKET_TR].end))
        self.assertEqual((4000, 4999), (ranges[REALTIME].start, ranges[REALTIME].end))
        self.assertEqual((5000, 5999), (ranges[ORDER].start, ranges[ORDER].end))
        self.assertEqual((9000, 9999), (ranges[ADMIN_GUI].start, ranges[ADMIN_GUI].end))

        intervals = sorted((screen_range.start, screen_range.end) for screen_range in ranges.values())
        for previous, current in zip(intervals, intervals[1:]):
            self.assertLess(previous[1], current[0])

    def test_deterministic_claim_release_and_reuse(self) -> None:
        allocator = KiwoomScreenAllocator()

        first = allocator.claim(MARKET_TR, "A")
        second = allocator.claim(MARKET_TR, "B")

        self.assertEqual("3000", first.screen_no)
        self.assertEqual("3001", second.screen_no)

        allocator.release("A", first.screen_no)
        reused = allocator.claim(MARKET_TR, "C")

        self.assertEqual("3000", reused.screen_no)
        self.assertTrue(allocator.is_leased("3000"))

    def test_active_lease_collision_is_blocked(self) -> None:
        allocator = KiwoomScreenAllocator()
        allocator.claim(ACCOUNT_TR, "A", requested_screen_no="1000")

        with self.assertRaises(ScreenAllocationError) as raised:
            allocator.claim(ACCOUNT_TR, "B", requested_screen_no="1000")

        self.assertEqual(SCREEN_ALREADY_LEASED, raised.exception.error_kind)

    def test_requested_screen_must_belong_to_purpose(self) -> None:
        allocator = KiwoomScreenAllocator()

        with self.assertRaises(ScreenAllocationError) as raised:
            allocator.claim(MARKET_TR, "A", requested_screen_no="9001")

        self.assertEqual(SCREEN_OUT_OF_RANGE, raised.exception.error_kind)

    def test_pool_exhaustion_fails_closed(self) -> None:
        allocator = KiwoomScreenAllocator()
        for index in range(1000):
            allocator.claim(MARKET_TR, f"owner-{index}")

        with self.assertRaises(ScreenAllocationError) as raised:
            allocator.claim(MARKET_TR, "overflow")

        self.assertEqual(SCREEN_POOL_EXHAUSTED, raised.exception.error_kind)

    def test_order_default_uses_project_order_namespace(self) -> None:
        self.assertEqual("5000", project_order_default_screen_no())


if __name__ == "__main__":
    unittest.main()
