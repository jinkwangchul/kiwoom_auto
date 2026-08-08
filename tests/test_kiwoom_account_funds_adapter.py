# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

from kiwoom_account_funds_adapter import KiwoomAccountFundsAdapter


class _DeferredApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def request_account_funds_snapshot(self, account_id, *, request_id, callback):
        self.calls.append(
            {"account_id": account_id, "request_id": request_id, "callback": callback}
        )
        return {"ok": True, "status": "REQUESTED"}


class KiwoomAccountFundsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = _DeferredApi()
        self.adapter = KiwoomAccountFundsAdapter(self.api)
        self.adapter.set_active_account("1234567890")

    def request(self, request_id: int, results: list[dict[str, object]]):
        return self.adapter.request_account_funds(
            "1234567890",
            request_id=request_id,
            callback=results.append,
        )

    def test_normalizes_money_and_account_type(self) -> None:
        results: list[dict[str, object]] = []
        self.request(1, results)
        self.api.calls[0]["callback"](
            {
                "ok": True,
                "account_id": "1234567890",
                "raw_deposit": " +001,250,000 ",
                "raw_orderable_cash": "-00000100",
                "account_type": "REAL",
            }
        )
        self.assertEqual(1_250_000, results[0]["deposit"])
        self.assertEqual(-100, results[0]["orderable_cash"])
        self.assertEqual("실계좌", results[0]["account_type"])

    def test_zero_is_ready_but_blank_or_nonnumeric_is_failed(self) -> None:
        for request_id, deposit, orderable, expected_ok in (
            (1, "0", "000", True),
            (2, "", "100", False),
            (3, "100", "", False),
            (4, "invalid", "100", False),
        ):
            with self.subTest(request_id=request_id):
                api = _DeferredApi()
                adapter = KiwoomAccountFundsAdapter(api)
                adapter.set_active_account("1234567890")
                results: list[dict[str, object]] = []
                adapter.request_account_funds(
                    "1234567890", request_id=request_id, callback=results.append
                )
                api.calls[0]["callback"](
                    {"ok": True, "account_id": "1234567890",
                     "raw_deposit": deposit, "raw_orderable_cash": orderable,
                     "account_type": "SIMULATION"}
                )
                self.assertIs(expected_ok, results[0]["ok"])
                if expected_ok:
                    self.assertEqual(0, results[0]["deposit"])
                    self.assertEqual("모의투자", results[0]["account_type"])

    def test_unknown_server_type_remains_unclassified(self) -> None:
        results: list[dict[str, object]] = []
        self.request(1, results)
        self.api.calls[0]["callback"](
            {"ok": True, "account_id": "1234567890", "raw_deposit": "100",
             "raw_orderable_cash": "90", "account_type": ""}
        )
        self.assertEqual("", results[0]["account_type"])

    def test_repeated_requests_are_coalesced_to_one_followup(self) -> None:
        first_results: list[dict[str, object]] = []
        second_results: list[dict[str, object]] = []
        third_results: list[dict[str, object]] = []
        self.request(1, first_results)
        second = self.request(2, second_results)
        third = self.request(3, third_results)
        self.assertEqual("COALESCED", second["status"])
        self.assertEqual("COALESCED", third["status"])
        self.assertEqual(1, len(self.api.calls))

        self.api.calls[0]["callback"](
            {"ok": True, "account_id": "1234567890", "raw_deposit": "10",
             "raw_orderable_cash": "9", "account_type": "REAL"}
        )
        self.assertEqual(2, len(self.api.calls))
        self.assertEqual(3, self.api.calls[1]["request_id"])
        self.assertEqual([], second_results)

        self.api.calls[1]["callback"](
            {"ok": False, "account_id": "1234567890", "error": "failed"}
        )
        self.assertEqual(1, len(first_results))
        self.assertEqual(1, len(third_results))

    def test_account_change_drops_old_queued_followup(self) -> None:
        results: list[dict[str, object]] = []
        self.request(1, results)
        self.request(2, results)
        self.adapter.set_active_account("2222222222")
        self.api.calls[0]["callback"](
            {"ok": True, "account_id": "1234567890", "raw_deposit": "10",
             "raw_orderable_cash": "9", "account_type": "REAL"}
        )
        self.assertEqual(1, len(self.api.calls))


if __name__ == "__main__":
    unittest.main()
