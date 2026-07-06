# -*- coding: utf-8 -*-
"""order_signal_test_helper.py

루틴 BUY/SELL 신호를 order_manager에 넣어보는 개발/검증용 보조 스크립트.

목적:
- 실제 키움 주문을 보내지 않는다.
- 루틴 구현 전에도 메인 주문판정 계층(order_manager)의 동작을 확인한다.
- 기본 실행은 판정만 수행하고 state.json을 저장하지 않는다.
- --accept 옵션을 붙인 경우에만 dry-run 주문 접수 성공으로 가정하고,
  조기/자동마감 루틴 방식의 첫 SELL 메타 저장까지 테스트한다.

사용 예:
    python order_signal_test_helper.py 000660 BUY
    python order_signal_test_helper.py 000660 SELL
    python order_signal_test_helper.py 000660 SELL --accept

주의:
- --accept는 state.json을 변경할 수 있다.
- 이 파일은 테스트 보조 도구이며 실제 주문 실행기가 아니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from order_manager import (
    decide_routine_order_for_stock_dir,
    handle_routine_signal_dry_run_for_stock_dir,
)
from runtime_io import read_json_dict


def find_stock_dir_by_code(project_root: Path, stock_code: str) -> Path | None:
    """프로젝트 폴더 안에서 종목코드로 state.json이 있는 종목 폴더를 찾는다."""
    code = str(stock_code or "").strip()
    if not code:
        return None

    for state_path in project_root.glob(f"**/{code}_*/state.json"):
        if state_path.is_file():
            return state_path.parent
    return None


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """콘솔 확인용으로 핵심 결과만 추린다."""
    keys = [
        "allowed",
        "signal_type",
        "reason",
        "mark_close_final_sell_after_order",
        "order_executor_called",
        "order_accepted",
        "state_saved",
        "stock_dir",
    ]
    return {key: result.get(key) for key in keys if key in result}


def main() -> int:
    parser = argparse.ArgumentParser(description="루틴 BUY/SELL 신호 dry-run 테스트")
    parser.add_argument("stock_code", help="종목코드 예: 000660")
    parser.add_argument("signal", choices=["BUY", "SELL", "매수", "매도"], help="루틴 신호")
    parser.add_argument(
        "--accept",
        action="store_true",
        help="dry-run 주문 접수 성공으로 가정하고 필요한 state.json 메타를 저장한다.",
    )
    parser.add_argument(
        "--display-status",
        default="",
        help="표시 상태를 강제로 넘길 때 사용. 보통 비워둔다.",
    )
    args = parser.parse_args()

    stock_dir = find_stock_dir_by_code(PROJECT_ROOT, args.stock_code)
    if stock_dir is None:
        print(json.dumps({"ok": False, "error": "종목 폴더를 찾지 못했습니다.", "stock_code": args.stock_code}, ensure_ascii=False, indent=2))
        return 2

    before_state = read_json_dict(stock_dir / "state.json")
    before_marker = {
        "status": before_state.get("status"),
        "buy_enabled": before_state.get("buy_enabled"),
        "sell_enabled": before_state.get("sell_enabled"),
        "close_routine_final_sell_ordered": before_state.get("close_routine_final_sell_ordered"),
        "close_routine_final_sell_ordered_at": before_state.get("close_routine_final_sell_ordered_at"),
    }

    if args.accept:
        result = handle_routine_signal_dry_run_for_stock_dir(
            stock_dir,
            args.signal,
            display_status=args.display_status,
            source="order_signal_test_helper",
        )
    else:
        result = decide_routine_order_for_stock_dir(
            stock_dir,
            args.signal,
            display_status=args.display_status,
        )

    after_state = read_json_dict(stock_dir / "state.json")
    after_marker = {
        "status": after_state.get("status"),
        "buy_enabled": after_state.get("buy_enabled"),
        "sell_enabled": after_state.get("sell_enabled"),
        "close_routine_final_sell_ordered": after_state.get("close_routine_final_sell_ordered"),
        "close_routine_final_sell_ordered_at": after_state.get("close_routine_final_sell_ordered_at"),
    }

    print(json.dumps(
        {
            "ok": True,
            "mode": "dry_run_accept_and_save" if args.accept else "decision_only_no_save",
            "stock_dir": str(stock_dir),
            "before": before_marker,
            "decision": compact_result(result),
            "after": after_marker,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
