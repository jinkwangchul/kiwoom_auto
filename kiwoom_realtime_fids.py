# -*- coding: utf-8 -*-
"""Officially verified Kiwoom realtime FIDs used by shadow mode."""

# KOA StudioSA 2.34 Real Type "주식체결"; installed koa_devguide.xml
# GetCommRealData example at lines 629-652 (CP949 source).
REALTIME_EXECUTION_TIME_FID = 20  # 체결시간
REALTIME_CURRENT_PRICE_FID = 10  # 현재가
REALTIME_CUMULATIVE_VOLUME_FID = 13  # 누적거래량
REALTIME_EXECUTION_TYPE = "주식체결"

REALTIME_SHADOW_FIDS = (
    REALTIME_EXECUTION_TIME_FID,
    REALTIME_CURRENT_PRICE_FID,
    REALTIME_CUMULATIVE_VOLUME_FID,
)
