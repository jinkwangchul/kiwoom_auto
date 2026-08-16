# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gui_auto_trade_close as close
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_auto_trade_status_ops import auto_trade_update_stock_status
from gui_review_utils import merge_existing_review_metadata
from runtime_stock_state_mutation import mutate_runtime_stock_state


class ReviewReasonMergeContractTest(unittest.TestCase):
    def _stock_dir(self, root: str, state: dict[str, object]) -> Path:
        stock_dir = Path(root) / "000001_TEST"
        stock_dir.mkdir(parents=True)
        (stock_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return stock_dir

    def test_follow_up_reasons_are_unique_ordered_and_preserve_first_identity(self) -> None:
        state = {
            "status": "REVIEW_REQUIRED",
            "review_required": True,
            "review_status": "RESOLVED",
            "review_reason": "운영 데이터 불일치 / 운영 데이터 불일치",
            "review_detail": "first evidence",
            "review_location": "운영 시작",
            "review_entered_at": "2026-08-01 09:00:00",
        }
        second = merge_existing_review_metadata(
            state,
            {
                "review_status": "PENDING",
                "review_reason": "미체결 데이터 오류",
                "review_detail": "pending evidence",
                "review_location": "종목 등록",
                "review_entered_at": "2026-08-16 10:00:00",
            },
        )
        state.update(second)
        third = merge_existing_review_metadata(
            state,
            {
                "review_reason": "청산 후 보유잔량 / 미체결 데이터 오류",
                "review_detail": "close evidence",
                "review_location": "운영 중",
            },
        )

        self.assertEqual(
            "운영 데이터 불일치 / 미체결 데이터 오류 / 청산 후 보유잔량",
            third["review_reason"],
        )
        self.assertEqual("2026-08-01 09:00:00", third["review_entered_at"])
        self.assertEqual("운영 시작", third["review_location"])
        self.assertEqual("RESOLVED", third["review_status"])
        self.assertEqual(
            "first evidence\npending evidence\nclose evidence",
            third["review_detail"],
        )

    def test_canonical_runtime_writer_merges_close_reason(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_reason": "운영 데이터 불일치",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                },
            )
            result = mutate_runtime_stock_state(
                stock_dir,
                "REVIEW_REQUIRED",
                {
                    "review_required": True,
                    "review_reason": "청산 후 보유잔량",
                    "review_location": "운영 중",
                },
                updated_at="2026-08-16 10:00:00",
                verify_readback=True,
            )
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(result.ok)
        self.assertEqual(
            "운영 데이터 불일치 / 청산 후 보유잔량",
            saved["review_reason"],
        )
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])

    def test_non_review_virtual_or_normal_state_is_not_implicitly_merged(self) -> None:
        metadata = merge_existing_review_metadata(
            {},
            {"review_reason": "미체결 데이터 오류", "review_location": "종목 등록"},
        )
        self.assertEqual("미체결 데이터 오류", metadata["review_reason"])
        self.assertNotIn("review_entered_at", metadata)

    def test_close_writer_merges_processing_error_through_existing_status_writer(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_reason": "운영 데이터 불일치",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                },
            )

            class Window:
                _operation_start_batch_active = False

                def update_stock_status(self, *args, **kwargs):
                    return auto_trade_update_stock_status(self, *args, **kwargs)

            with patch("gui_auto_trade_status_ops.append_stock_log"):
                ok = close._persist_early_close_execution_result(
                    Window(),
                    stock_dir=stock_dir,
                    code="000001",
                    name="테스트",
                    result={
                        "ok": False,
                        "stage": "execution",
                        "runtime_status": "REVIEW_REQUIRED",
                    },
                )
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(
            "운영 데이터 불일치 / 청산 처리 오류",
            saved["review_reason"],
        )
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])

    def test_recovery_integrity_writer_merges_official_reason(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stock_dir = self._stock_dir(
                root,
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_reason": "운영 데이터 불일치",
                    "review_location": "운영 시작",
                    "review_entered_at": "2026-08-01 09:00:00",
                },
            )

            class Writer:
                _operation_start_batch_active = False

                def update_stock_status(self, *args, **kwargs):
                    return auto_trade_update_stock_status(self, *args, **kwargs)

            item = {
                "review_reasons": ["RECOVERY_STATE_INVALID"],
                "review_location": "PRODUCTION_RECOVERY",
            }
            with patch("gui_auto_trade_status_ops.append_stock_log"):
                ok = AutoTradeOperationHost.mark_review_required(
                    Writer(), stock_dir, "000001", "테스트", item,
                    source="PRODUCTION_RECOVERY",
                )
            saved = json.loads((stock_dir / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(
            "운영 데이터 불일치 / 복구 상태 오류",
            saved["review_reason"],
        )
        self.assertEqual("운영 시작", saved["review_location"])
        self.assertEqual("2026-08-01 09:00:00", saved["review_entered_at"])


if __name__ == "__main__":
    unittest.main()
