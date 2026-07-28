from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import unittest

from production_recovery_contract import (
    ACCOUNT_COMPLETED,
    ACCOUNT_REVIEW_REQUIRED,
    ACCOUNT_RECONCILING,
    RECOVERY_GATE_ACCOUNT_INCOMPLETE,
    RECOVERY_GATE_ALLOWED,
    RECOVERY_GATE_IDENTITY_MISMATCH,
    RECOVERY_GATE_STALE,
    STOCK_PENDING,
    STOCK_RESTORED,
    build_snapshot_part,
    combine_account_snapshot,
    create_recovery_session_identity,
    decide_recovery_gate,
    decide_account_recovery,
    decide_stock_recovery,
    parse_holding_rows,
    parse_open_order_rows,
)


class ProductionRecoveryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = create_recovery_session_identity(
            login_session_id="KIWOOM_LOGIN_SESSION_TEST",
            account_no="1234567890",
            trading_day="2026-07-27",
            requested_at="2026-07-27T09:00:00.123456",
        )

    def holding_row(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "종목번호": "A005930",
            "종목명": "삼성전자",
            "평가손익": "1000",
            "수익률(%)": "1.25",
            "매입가": "70000",
            "보유수량": "10",
            "매매가능수량": "8",
            "현재가": "+71000",
            "평가금액": "710000",
        }
        row.update(overrides)
        return row

    def order_row(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "계좌번호": "1234567890",
            "주문번호": "10001",
            "종목코드": "005930",
            "주문상태": "접수",
            "종목명": "삼성전자",
            "주문수량": "10",
            "주문가격": "70000",
            "미체결수량": "4",
            "원주문번호": "",
            "주문구분": "+매수",
            "매매구분": "2",
            "시간": "091500",
            "체결량": "6",
        }
        row.update(overrides)
        return row

    def test_session_identity_is_deterministic_for_same_attempt(self) -> None:
        repeated = create_recovery_session_identity(
            login_session_id=self.identity.login_session_id,
            account_no=self.identity.account_no,
            trading_day=self.identity.trading_day,
            requested_at=self.identity.requested_at,
        )
        self.assertEqual(self.identity, repeated)

    def test_holding_parser_normalizes_code_prices_and_decimals(self) -> None:
        items, errors = parse_holding_rows(
            [self.holding_row()],
            account_no=self.identity.account_no,
        )
        self.assertEqual((), errors)
        self.assertEqual("005930", items[0].stock_code)
        self.assertEqual(Decimal("70000"), items[0].average_price)
        self.assertEqual(Decimal("71000"), items[0].current_price)

    def test_duplicate_holding_is_incomplete_evidence(self) -> None:
        _items, errors = parse_holding_rows(
            [self.holding_row(), self.holding_row()],
            account_no=self.identity.account_no,
        )
        self.assertTrue(any("duplicate stock_code" in error for error in errors))

    def test_open_order_parser_normalizes_partial_fill(self) -> None:
        items, errors = parse_open_order_rows(
            [self.order_row()],
            account_no=self.identity.account_no,
        )
        self.assertEqual((), errors)
        self.assertEqual("BUY", items[0].order_side)
        self.assertEqual("NEW", items[0].order_type)
        self.assertEqual(6, items[0].filled_quantity)
        self.assertEqual(4, items[0].unfilled_quantity)

    def test_cancel_order_keeps_original_order_relationship(self) -> None:
        items, errors = parse_open_order_rows(
            [
                self.order_row(
                    주문번호="10002",
                    원주문번호="10001",
                    주문구분="매수취소",
                )
            ],
            account_no=self.identity.account_no,
        )
        self.assertEqual((), errors)
        self.assertEqual("CANCEL", items[0].order_type)
        self.assertEqual("10001", items[0].original_order_no)

    def test_account_mismatch_and_duplicate_order_are_errors(self) -> None:
        _items, mismatch = parse_open_order_rows(
            [self.order_row(계좌번호="9999999999")],
            account_no=self.identity.account_no,
        )
        self.assertTrue(any("account_no mismatch" in error for error in mismatch))
        _items, duplicate = parse_open_order_rows(
            [self.order_row(), self.order_row()],
            account_no=self.identity.account_no,
        )
        self.assertTrue(any("duplicate broker_order_no" in error for error in duplicate))

    def test_incomplete_collection_never_becomes_complete_snapshot(self) -> None:
        part = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row()],
            completed_at=datetime.now().isoformat(timespec="microseconds"),
            source="TEST",
            collection_complete=False,
        )
        self.assertFalse(part.is_complete)
        self.assertIn("broker snapshot collection is incomplete", part.errors)

    def test_account_snapshot_requires_both_complete_parts(self) -> None:
        completed_at = datetime.now().isoformat(timespec="microseconds")
        holdings = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        orders = build_snapshot_part(
            identity=self.identity,
            kind="OPEN_ORDERS",
            rows=[self.order_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=False,
        )
        snapshot = combine_account_snapshot(self.identity, holdings, orders)
        self.assertFalse(snapshot.is_complete)

    def test_gate_allows_only_completed_account_and_restored_stock(self) -> None:
        decision = decide_recovery_gate(
            identity=self.identity,
            expected_login_session_id=self.identity.login_session_id,
            expected_account_no=self.identity.account_no,
            expected_trading_day=self.identity.trading_day,
            expected_recovery_session_id=self.identity.recovery_session_id,
            account_status=ACCOUNT_COMPLETED,
            stock_status=STOCK_RESTORED,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(RECOVERY_GATE_ALLOWED, decision.reason_code)

    def test_gate_blocks_account_in_progress(self) -> None:
        decision = decide_recovery_gate(
            identity=self.identity,
            expected_login_session_id=self.identity.login_session_id,
            expected_account_no=self.identity.account_no,
            expected_trading_day=self.identity.trading_day,
            expected_recovery_session_id=self.identity.recovery_session_id,
            account_status=ACCOUNT_RECONCILING,
            stock_status=STOCK_PENDING,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(RECOVERY_GATE_ACCOUNT_INCOMPLETE, decision.reason_code)

    def test_gate_blocks_other_account_and_stale_day(self) -> None:
        other_account = decide_recovery_gate(
            identity=self.identity,
            expected_login_session_id=self.identity.login_session_id,
            expected_account_no="9999999999",
            expected_trading_day=self.identity.trading_day,
            expected_recovery_session_id=self.identity.recovery_session_id,
            account_status=ACCOUNT_COMPLETED,
            stock_status=STOCK_RESTORED,
        )
        self.assertEqual(RECOVERY_GATE_IDENTITY_MISMATCH, other_account.reason_code)
        stale = decide_recovery_gate(
            identity=self.identity,
            expected_login_session_id=self.identity.login_session_id,
            expected_account_no=self.identity.account_no,
            expected_trading_day="2026-07-28",
            expected_recovery_session_id=self.identity.recovery_session_id,
            account_status=ACCOUNT_COMPLETED,
            stock_status=STOCK_RESTORED,
        )
        self.assertEqual(RECOVERY_GATE_STALE, stale.reason_code)

    def test_stock_recovery_matches_holding_and_open_order(self) -> None:
        completed_at = datetime.now().isoformat(timespec="microseconds")
        holdings = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        orders = build_snapshot_part(
            identity=self.identity,
            kind="OPEN_ORDERS",
            rows=[self.order_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        snapshot = combine_account_snapshot(self.identity, holdings, orders)
        result = decide_stock_recovery(
            snapshot=snapshot,
            stock_code="005930",
            runtime_position={
                "account_no": self.identity.account_no,
                "code": "005930",
                "quantity": 10,
                "available_quantity": 8,
                "average_price": 70000,
            },
            runtime_orders=[
                {
                    "account_no": self.identity.account_no,
                    "code": "005930",
                    "status": "BROKER_ACCEPTED",
                    "broker_order_no": "10001",
                    "side": "BUY",
                    "quantity": 10,
                    "remaining_quantity": 4,
                    "original_order_no": "",
                }
            ],
        )
        self.assertEqual(STOCK_RESTORED, result.status)
        self.assertTrue(result.holding_matched)
        self.assertTrue(result.order_matched)

    def test_stock_recovery_routes_mismatch_to_review(self) -> None:
        completed_at = datetime.now().isoformat(timespec="microseconds")
        holdings = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        orders = build_snapshot_part(
            identity=self.identity,
            kind="OPEN_ORDERS",
            rows=[],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        snapshot = combine_account_snapshot(self.identity, holdings, orders)
        result = decide_stock_recovery(
            snapshot=snapshot,
            stock_code="005930",
            runtime_position={
                "account_no": self.identity.account_no,
                "code": "005930",
                "quantity": 9,
                "average_price": 70000,
            },
            runtime_orders=[],
        )
        self.assertEqual("REVIEW_REQUIRED", result.status)
        self.assertIn("HOLDING_QUANTITY_MISMATCH", result.reason_codes)

    def test_stock_recovery_uses_exact_decimal_average_price(self) -> None:
        completed_at = datetime.now().isoformat(timespec="microseconds")
        holdings = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row(매입가="70000.1")],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        orders = build_snapshot_part(
            identity=self.identity,
            kind="OPEN_ORDERS",
            rows=[],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        snapshot = combine_account_snapshot(self.identity, holdings, orders)
        result = decide_stock_recovery(
            snapshot=snapshot,
            stock_code="005930",
            runtime_position={
                "account_no": self.identity.account_no,
                "code": "005930",
                "quantity": 10,
                "average_price": "70000.0",
            },
            runtime_orders=[],
        )
        self.assertIn("AVERAGE_PRICE_MISMATCH", result.reason_codes)

    def test_account_recovery_aggregates_stock_review(self) -> None:
        completed_at = datetime.now().isoformat(timespec="microseconds")
        holdings = build_snapshot_part(
            identity=self.identity,
            kind="HOLDINGS",
            rows=[self.holding_row()],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        orders = build_snapshot_part(
            identity=self.identity,
            kind="OPEN_ORDERS",
            rows=[],
            completed_at=completed_at,
            source="TEST",
            collection_complete=True,
        )
        snapshot = combine_account_snapshot(self.identity, holdings, orders)
        stock = decide_stock_recovery(
            snapshot=snapshot,
            stock_code="005930",
            runtime_position=None,
            runtime_orders=[],
        )
        result = decide_account_recovery(
            identity=self.identity,
            snapshot=snapshot,
            stock_results=[stock],
        )
        self.assertEqual(ACCOUNT_REVIEW_REQUIRED, result.status)
        self.assertTrue(result.review_required)


if __name__ == "__main__":
    unittest.main()
