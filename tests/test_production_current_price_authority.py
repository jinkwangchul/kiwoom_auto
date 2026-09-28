from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from production_current_price_authority import (
    KRX_CURRENT_PRICE_SOURCE,
    KRX_REGULAR_AUTHORITY,
    NXT_AFTER_HOURS_AUTHORITY,
    NXT_CURRENT_PRICE_SOURCE,
    NXT_MORNING_AUTHORITY,
    production_current_price_authority,
    select_production_current_price_evidence,
)


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(
        2026,
        8,
        20,
        hour,
        minute,
        second,
        tzinfo=SEOUL_TIMEZONE,
    )


def _krx_state(
    *,
    market_datetime: str = "2026-08-20T10:15:01+09:00",
    price: object = 70_000,
    epoch: int = 7,
    session_id: str = "SESSION-7",
    broker_code_identity: str = "005930",
    market_source: str = "KRX",
    sequence: int = 10,
):
    return SimpleNamespace(
        stock_code="005930",
        broker_code_identity=broker_code_identity,
        market_source=market_source,
        connection_epoch=epoch,
        login_session_id=session_id,
        last_market_datetime=market_datetime,
        last_price=price,
        last_receive_sequence=sequence,
        last_received_at="2026-08-20T10:15:01.000001+09:00",
    )


def _nxt_state(
    *,
    market_datetime: str = "2026-08-20T18:00:01+09:00",
    price: object = 261_000,
    epoch: int = 7,
    session_id: str = "SESSION-7",
    broker_code_identity: str = "005930_NX",
    market_source: str = "NXT",
    source_real_type: str = "ECN주식체결",
    sequence: int = 20,
):
    return SimpleNamespace(
        canonical_stock_code="005930",
        broker_code_identity=broker_code_identity,
        market_source=market_source,
        source_real_type=source_real_type,
        connection_epoch=epoch,
        login_session_id=session_id,
        last_market_datetime=market_datetime,
        last_price=price,
        receive_sequence=sequence,
        updated_at="2026-08-20T18:00:01.000001+09:00",
    )


class ProductionCurrentPriceAuthorityTests(unittest.TestCase):
    def test_authority_windows_are_source_exclusive(self) -> None:
        expected = (
            (8, 0, True, NXT_CURRENT_PRICE_SOURCE, NXT_MORNING_AUTHORITY),
            (8, 49, True, NXT_CURRENT_PRICE_SOURCE, NXT_MORNING_AUTHORITY),
            (8, 50, True, None, None),
            (8, 59, True, None, None),
            (9, 0, True, KRX_CURRENT_PRICE_SOURCE, KRX_REGULAR_AUTHORITY),
            (15, 29, True, KRX_CURRENT_PRICE_SOURCE, KRX_REGULAR_AUTHORITY),
            (15, 30, True, None, None),
            (15, 39, True, None, None),
            (15, 40, True, NXT_CURRENT_PRICE_SOURCE, NXT_AFTER_HOURS_AUTHORITY),
            (19, 59, True, NXT_CURRENT_PRICE_SOURCE, NXT_AFTER_HOURS_AUTHORITY),
            (20, 0, True, None, None),
        )
        for hour, minute, eligible, source, window in expected:
            with self.subTest(hour=hour, minute=minute):
                authority = production_current_price_authority(
                    now_dt=_at(hour, minute),
                    nxt_available=eligible,
                )
                self.assertEqual(source, getattr(authority, "market_source", None))
                self.assertEqual(window, getattr(authority, "authority_window", None))

    def test_nxt_window_requires_verified_eligibility(self) -> None:
        for availability in (False, None):
            with self.subTest(availability=availability):
                self.assertIsNone(
                    production_current_price_authority(
                        now_dt=_at(18, 0),
                        nxt_available=availability,
                    )
                )

    def test_krx_evidence_is_projected_only_in_krx_window(self) -> None:
        evidence = select_production_current_price_evidence(
            canonical_stock_code="005930",
            connection_epoch=7,
            login_session_id="SESSION-7",
            nxt_available=True,
            krx_state=_krx_state(),
            nxt_state=_nxt_state(),
            now_dt=_at(10, 15, 2),
        )
        self.assertIsNotNone(evidence)
        self.assertEqual(KRX_CURRENT_PRICE_SOURCE, evidence.market_source)
        self.assertEqual("005930", evidence.broker_code_identity)
        self.assertEqual("주식체결", evidence.source_real_type)
        self.assertEqual(70_000, evidence.current_price)
        self.assertEqual(KRX_REGULAR_AUTHORITY, evidence.authority_window)

    def test_nxt_evidence_is_projected_only_in_nxt_window(self) -> None:
        evidence = select_production_current_price_evidence(
            canonical_stock_code="005930",
            connection_epoch=7,
            login_session_id="SESSION-7",
            nxt_available=True,
            krx_state=_krx_state(),
            nxt_state=_nxt_state(),
            now_dt=_at(18, 0, 2),
        )
        self.assertIsNotNone(evidence)
        self.assertEqual(NXT_CURRENT_PRICE_SOURCE, evidence.market_source)
        self.assertEqual("005930_NX", evidence.broker_code_identity)
        self.assertEqual("ECN주식체결", evidence.source_real_type)
        self.assertEqual(261_000, evidence.current_price)
        self.assertEqual(NXT_AFTER_HOURS_AUTHORITY, evidence.authority_window)

    def test_authority_gap_never_falls_back_to_other_source(self) -> None:
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(),
                nxt_state=_nxt_state(),
                now_dt=_at(15, 35),
            )
        )

    def test_source_transition_rejects_pre_window_tick(self) -> None:
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(),
                nxt_state=_nxt_state(
                    market_datetime="2026-08-20T15:39:59+09:00",
                ),
                now_dt=_at(15, 40, 1),
            )
        )
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(
                    market_datetime="2026-08-20T08:59:59+09:00",
                ),
                nxt_state=_nxt_state(),
                now_dt=_at(9, 0, 1),
            )
        )

    def test_future_market_time_is_rejected(self) -> None:
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(
                    market_datetime="2026-08-20T10:16:00+09:00",
                ),
                nxt_state=None,
                now_dt=_at(10, 15, 59),
            )
        )

    def test_session_mismatch_is_rejected(self) -> None:
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(session_id="STALE"),
                nxt_state=None,
                now_dt=_at(10, 15, 2),
            )
        )

    def test_nxt_identity_and_real_type_are_strict(self) -> None:
        for state in (
            _nxt_state(broker_code_identity="005930"),
            _nxt_state(market_source="KRX"),
            _nxt_state(source_real_type="주식체결"),
        ):
            with self.subTest(state=state):
                self.assertIsNone(
                    select_production_current_price_evidence(
                        canonical_stock_code="005930",
                        connection_epoch=7,
                        login_session_id="SESSION-7",
                        nxt_available=True,
                        krx_state=None,
                        nxt_state=state,
                        now_dt=_at(18, 0, 2),
                    )
                )

    def test_incomplete_provenance_and_nonbroker_code_fail_closed(self) -> None:
        missing_received_at = _krx_state()
        missing_received_at.last_received_at = ""
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="005930",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=missing_received_at,
                nxt_state=None,
                now_dt=_at(10, 15, 2),
            )
        )
        self.assertIsNone(
            select_production_current_price_evidence(
                canonical_stock_code="0009K0",
                connection_epoch=7,
                login_session_id="SESSION-7",
                nxt_available=True,
                krx_state=_krx_state(),
                nxt_state=None,
                now_dt=_at(10, 15, 2),
            )
        )

    def test_invalid_price_and_sequence_fail_closed(self) -> None:
        for state in (
            _krx_state(price=0),
            _krx_state(price=float("nan")),
            _krx_state(sequence=0),
        ):
            with self.subTest(state=state):
                self.assertIsNone(
                    select_production_current_price_evidence(
                        canonical_stock_code="005930",
                        connection_epoch=7,
                        login_session_id="SESSION-7",
                        nxt_available=True,
                        krx_state=state,
                        nxt_state=None,
                        now_dt=_at(10, 15, 2),
                    )
                )


if __name__ == "__main__":
    unittest.main()
