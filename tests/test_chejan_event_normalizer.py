# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import chejan_event_normalizer
from chejan_event_normalizer import normalize_kiwoom_chejan_event


class ChejanEventNormalizerTest(unittest.TestCase):
    def _raw(self, **fid_overrides: object) -> dict[str, object]:
        fid_values = {
            "9201": "12345678",
            "9203": "12345",
            "9001": "A003550",
            "302": "LG",
            "907": "2",
            "913": "체결",
            "900": "10",
            "911": "3",
            "902": "7",
            "910": "1000",
            "901": "1000",
        }
        fid_values.update(fid_overrides)
        return {
            "source": "kiwoom_chejan",
            "gubun": "0",
            "fid_values": fid_values,
            "received_at": "2026-07-04 10:00:00",
        }

    def test_raw_event_non_dict_is_blocked(self) -> None:
        result = normalize_kiwoom_chejan_event(None)

        self.assertFalse(result["normalized"])
        self.assertEqual("chejan_event_blocked", result["event_stage"])
        self.assertTrue(result["unresolved"])
        self.assertIn("raw_event must be a dict", result["blocked_reasons"])

    def test_fid_values_missing_is_blocked(self) -> None:
        result = normalize_kiwoom_chejan_event(
            {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "received_at": "2026-07-04 10:00:00",
            }
        )

        self.assertFalse(result["normalized"])
        self.assertIn("raw_event.fid_values must be a dict", result["blocked_reasons"])

    def test_missing_source_gubun_or_received_at_is_blocked(self) -> None:
        cases = (
            ({"source": "", "gubun": "0"}, "raw_event.source is required"),
            ({"source": "kiwoom_chejan", "gubun": ""}, "raw_event.gubun must be 0"),
            ({"source": "kiwoom_chejan", "gubun": "0"}, "raw_event.received_at is required"),
        )

        for overrides, expected_reason in cases:
            with self.subTest(overrides=overrides):
                raw = self._raw()
                raw.update(overrides)
                if "received_at" not in overrides:
                    raw.pop("received_at", None)
                result = normalize_kiwoom_chejan_event(raw)

                self.assertFalse(result["normalized"])
                self.assertTrue(result["unresolved"])
                self.assertIn(expected_reason, result["blocked_reasons"][0])

    def test_buy_partial_fill_normalizes_to_partial_fill(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw())

        self.assertTrue(result["normalized"])
        self.assertEqual("PARTIAL_FILL", result["event_type"])
        self.assertEqual("BUY", result["side"])
        self.assertFalse(result["unresolved"])

    def test_buy_full_fill_normalizes_to_full_fill(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"911": "10", "902": "0"}))

        self.assertEqual("FULL_FILL", result["event_type"])
        self.assertEqual("BUY", result["side"])

    def test_sell_partial_fill_normalizes_to_partial_fill(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"907": "1"}))

        self.assertEqual("PARTIAL_FILL", result["event_type"])
        self.assertEqual("SELL", result["side"])

    def test_open_order_normalizes_to_order_open(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"913": "접수", "911": "0", "902": "10"}))

        self.assertEqual("ORDER_OPEN", result["event_type"])
        self.assertFalse(result["unresolved"])

    def test_rejected_order_normalizes_to_order_rejected(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"913": "거부", "911": "0", "902": "10"}))

        self.assertEqual("ORDER_REJECTED", result["event_type"])
        self.assertFalse(result["unresolved"])

    def test_canceled_order_normalizes_to_order_canceled(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"913": "취소", "911": "0", "902": "0"}))

        self.assertEqual("ORDER_CANCELED", result["event_type"])
        self.assertFalse(result["unresolved"])

    def test_unknown_order_status_sets_unresolved(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"913": "알수없음", "911": "0", "902": "0"}))

        self.assertEqual("ORDER_UNKNOWN", result["event_type"])
        self.assertTrue(result["unresolved"])

    def test_code_removes_a_prefix(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"9001": " A003550 "}))

        self.assertEqual("003550", result["code"])

    def test_side_buy_and_sell_text_are_detected(self) -> None:
        buy_result = normalize_kiwoom_chejan_event(self._raw(**{"907": "매수"}))
        sell_result = normalize_kiwoom_chejan_event(self._raw(**{"907": "매도"}))

        self.assertEqual("BUY", buy_result["side"])
        self.assertEqual("SELL", sell_result["side"])

    def test_unclear_side_sets_unresolved_warning(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"907": "9"}))

        self.assertIsNone(result["side"])
        self.assertTrue(result["unresolved"])
        self.assertIn("side is unclear", result["warnings"])

    def test_numeric_strings_convert_to_ints(self) -> None:
        result = normalize_kiwoom_chejan_event(
            self._raw(**{"900": "1,000", "911": "3.0", "902": "997", "910": "1000", "901": "1000"})
        )

        self.assertEqual(1000, result["order_quantity"])
        self.assertEqual(3, result["filled_quantity"])
        self.assertEqual(997, result["remaining_quantity"])
        self.assertEqual(1000, result["filled_price"])
        self.assertEqual(1000, result["order_price"])

    def test_numeric_parse_failure_adds_warning(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"911": "abc"}))

        self.assertIsNone(result["filled_quantity"])
        self.assertTrue(result["unresolved"])
        self.assertIn("filled_quantity could not be parsed as int", result["warnings"])

    def test_blank_numeric_values_become_none_without_parse_warning(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"910": "-", "901": ""}))

        self.assertIsNone(result["filled_price"])
        self.assertIsNone(result["order_price"])
        self.assertNotIn("filled_price could not be parsed as int", result["warnings"])
        self.assertNotIn("order_price could not be parsed as int", result["warnings"])

    def test_core_identity_fields_are_preserved(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw(**{"904": "54321"}))

        self.assertEqual("12345", result["broker_order_no"])
        self.assertEqual("54321", result["original_order_no"])
        self.assertEqual("12345678", result["account_no"])
        self.assertEqual("LG", result["name"])
        self.assertEqual("체결", result["order_status"])
        self.assertEqual("kiwoom_chejan", result["source"])
        self.assertEqual("0", result["gubun"])
        self.assertEqual("2026-07-04 10:00:00", result["received_at"])

    def test_request_lock_execution_ids_remain_none(self) -> None:
        result = normalize_kiwoom_chejan_event(self._raw())

        self.assertIsNone(result["request_hash"])
        self.assertIsNone(result["lock_id"])
        self.assertIsNone(result["execution_id"])

    def test_raw_event_is_preserved(self) -> None:
        raw = self._raw()
        result = normalize_kiwoom_chejan_event(raw)

        self.assertEqual(raw, result["raw_event"])
        self.assertIsNot(raw, result["raw_event"])

    def test_input_dict_is_not_mutated(self) -> None:
        raw = self._raw()
        original = deepcopy(raw)

        normalize_kiwoom_chejan_event(raw)

        self.assertEqual(original, raw)

    def test_runtime_send_order_gui_timer_are_not_called(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = normalize_kiwoom_chejan_event(self._raw())

        self.assertTrue(result["normalized"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_module_does_not_reference_runtime_send_order_gui_or_timer(self) -> None:
        module_text = chejan_event_normalizer.__loader__.get_source(chejan_event_normalizer.__name__)

        self.assertNotIn("ORDER_QUEUE_PATH", module_text)
        self.assertNotIn("fills.json", module_text)
        self.assertNotIn("positions.json", module_text)
        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)


if __name__ == "__main__":
    unittest.main()
