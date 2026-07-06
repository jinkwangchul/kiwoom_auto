# -*- coding: utf-8 -*-
"""
공통 유틸 함수 모음.

UI, 정책 판단, 파일 수정 없이 값 변환만 담당한다.
"""

from __future__ import annotations


def safe_int_value(value: object, default: int = 0) -> int:
    """
    안전 정수 변환.
    """
    if value in (None, "", "-"):
        return default

    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default



def sanitize_path_part(value: str) -> str:
    """
    Windows 폴더명에 사용할 수 없는 문자를 안전하게 치환한다.
    """
    invalid_chars = '<>:"/\\|?*'
    result = str(value).strip()
    for char in invalid_chars:
        result = result.replace(char, "_")
    return result
