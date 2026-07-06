# -*- coding: utf-8 -*-
"""
runtime_io.py

종목별 런타임 JSON 파일(config/state/orders) 읽기·쓰기 공통 함수.
GUI 창 코드에서 파일 입출력 세부 처리를 분리한다.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_json_dict(path: Path) -> dict[str, object]:
    """
    JSON 파일을 dict 로 읽는다.
    오류 또는 dict 가 아닌 경우 빈 dict 를 반환한다.
    """
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    return data


def write_json_if_missing(path: Path, data: dict[str, object]) -> bool:
    """
    대상 JSON 파일이 없을 때만 기본값을 생성한다.
    기존 사용자가 저장한 설정은 덮어쓰지 않는다.
    """
    if path.exists():
        return False

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def read_orders_data(orders_path: Path) -> list[dict[str, object]]:
    """
    orders.json 을 주문 목록으로 읽는다.
    """
    data = read_json_dict(orders_path)
    orders = data.get("orders", [])
    if not isinstance(orders, list):
        return []

    return [order for order in orders if isinstance(order, dict)]
