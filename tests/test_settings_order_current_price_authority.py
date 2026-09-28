from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui_auto_trade_setting_window import AutoTradeSettingWindow


class _Host:
    def __init__(self, evidence):
        self.evidence = evidence
        self.evidence_calls = []
        self.legacy_calls = []

    def production_current_price_evidence(self, stock_code: str):
        self.evidence_calls.append(stock_code)
        return self.evidence

    def fresh_monitoring_market_information_state(self, stock_code: str):
        self.legacy_calls.append(stock_code)
        return SimpleNamespace(last_price=999_999)


class _Owner:
    def __init__(self, host):
        self.host = host
        self.kiwoom_api = None

    def main_monitoring_auto_trade_operation_host(self):
        return self.host

    def production_recovery_gate_for_stock(self, _stock_code: str, *, caller_name: str):
        return SimpleNamespace(ready=True, caller_name=caller_name)

    def current_orderable_cash_for_budget(self):
        return 1_000_000

    def selected_account_no(self):
        return "12345678"

    def kiwoom_account_numbers(self):
        return ["12345678"]


class _Window:
    def __init__(self, owner):
        self._owner = owner

    def parent(self):
        return self._owner

    def selected_stock_info(self):
        return None

    def current_selected_routine_row_metadata(self):
        return None

    def current_selected_target_instance_ids(self):
        return ()

    def current_selected_routine_dir(self):
        return None

    def confirm_execution_runtime_file_init(self, **_kwargs):
        return False


def _evidence(price=261_000):
    return SimpleNamespace(
        canonical_stock_code="005930",
        broker_code_identity="005930_NX",
        market_source="NXT",
        source_real_type="ECN주식체결",
        current_price=price,
        market_datetime="2026-09-28T15:40:01+09:00",
        received_at="2026-09-28T15:40:01.001+09:00",
        receive_sequence=17,
        connection_epoch=7,
        login_session_id="SESSION-7",
        authority_window="NXT_1540_2000",
    )


class SettingsOrderCurrentPriceAuthorityTest(unittest.TestCase):
    def _boundary(self, evidence):
        host = _Host(evidence)
        owner = _Owner(host)
        window = _Window(owner)
        boundary = AutoTradeSettingWindow.order_execution_boundary(window)
        return boundary, host

    def test_settings_boundary_uses_main_host_authority_evidence(self) -> None:
        boundary, host = self._boundary(_evidence())

        price = boundary._context.fresh_current_price("005930")
        evidence = boundary._context.fresh_current_price_evidence("005930")
        resolved_price, provenance, reason = boundary._fresh_current_price_with_provenance(
            "005930"
        )

        self.assertEqual(261_000, price)
        self.assertEqual(261_000, evidence.current_price)
        self.assertEqual(261_000, resolved_price)
        self.assertEqual("", reason)
        self.assertEqual("NXT", provenance["market_source"])
        self.assertEqual("005930_NX", provenance["broker_code_identity"])
        self.assertEqual("NXT_1540_2000", provenance["authority_window"])
        self.assertEqual([], host.legacy_calls)
        self.assertEqual(["005930", "005930", "005930"], host.evidence_calls)

    def test_missing_authority_evidence_never_falls_back_to_legacy_state(self) -> None:
        boundary, host = self._boundary(None)

        self.assertIsNone(boundary._context.fresh_current_price("005930"))
        price, provenance, reason = boundary._fresh_current_price_with_provenance(
            "005930"
        )

        self.assertIsNone(price)
        self.assertIsNone(provenance)
        self.assertIn("authority evidence is unavailable", reason)
        self.assertEqual([], host.legacy_calls)


if __name__ == "__main__":
    unittest.main()
