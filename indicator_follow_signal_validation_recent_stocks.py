"""V2-only recent-stock preference and read-only library projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import QSettings

from gui_stock_data import (
    STOCK_LIBRARY_READY,
    STOCK_LIBRARY_RUNTIME_SOURCE,
    StockLibraryLoadSnapshot,
    load_stock_library_snapshot,
)
from routines.지표추종매매.routine_validation_contract import ValidationStockRef


RECENT_STOCKS_SETTINGS_KEY = "ui/signal_validation_v2/recent_stocks"


def _default_settings() -> QSettings:
    return QSettings(
        QSettings.IniFormat,
        QSettings.UserScope,
        "jinkwangchul",
        "kiwoom_auto",
    )


def _normalized_identity(code: object, name: object) -> ValidationStockRef | None:
    normalized = ValidationStockRef(
        str(code or "").strip().upper(),
        str(name or "").strip(),
    )
    if not normalized.code or not normalized.name:
        return None
    return normalized


class IndicatorFollowSignalValidationRecentStockStore:
    """Own the single V2 QSettings writer and verified library read model."""

    def __init__(
        self,
        *,
        settings: object | None = None,
        snapshot_loader: Callable[[Path | None], StockLibraryLoadSnapshot] = (
            load_stock_library_snapshot
        ),
        project_root: str | Path | None = None,
    ) -> None:
        self._settings = settings if settings is not None else _default_settings()
        self._records_by_code = self._load_records(
            snapshot_loader,
            None if project_root is None else Path(project_root),
        )
        self._recent_stocks = self._load_recent_stocks()

    @staticmethod
    def _load_records(
        snapshot_loader: Callable[[Path | None], StockLibraryLoadSnapshot],
        project_root: Path | None,
    ) -> dict[str, dict[str, object]]:
        try:
            snapshot = snapshot_loader(project_root)
        except Exception:
            return {}
        if (
            not isinstance(snapshot, StockLibraryLoadSnapshot)
            or snapshot.state != STOCK_LIBRARY_READY
            or snapshot.source != STOCK_LIBRARY_RUNTIME_SOURCE
        ):
            return {}
        records: dict[str, dict[str, object]] = {}
        for raw_record in snapshot.records:
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            stock = _normalized_identity(record.get("code"), record.get("name"))
            if stock is None or stock.code in records:
                continue
            record["code"] = stock.code
            record["name"] = stock.name
            records[stock.code] = record
        return records

    def _load_recent_stocks(self) -> tuple[ValidationStockRef, ...]:
        try:
            raw_value = self._settings.value(RECENT_STOCKS_SETTINGS_KEY, "")
            payload = json.loads(str(raw_value or ""))
        except Exception:
            return ()
        if not isinstance(payload, list) or not self._records_by_code:
            return ()
        recent: list[ValidationStockRef] = []
        seen_codes: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            stored = _normalized_identity(item.get("code"), item.get("name"))
            if stored is None or stored.code in seen_codes:
                continue
            record = self._records_by_code.get(stored.code)
            if record is None:
                continue
            current = _normalized_identity(record.get("code"), record.get("name"))
            if current is None:
                continue
            recent.append(current)
            seen_codes.add(current.code)
        return tuple(recent)

    @property
    def recent_stocks(self) -> tuple[ValidationStockRef, ...]:
        return tuple(
            ValidationStockRef(stock.code, stock.name)
            for stock in self._recent_stocks
        )

    def metadata_for(self, stock: object) -> dict[str, object] | None:
        if not isinstance(stock, ValidationStockRef) or not stock.code:
            return None
        record = self._records_by_code.get(str(stock.code).strip().upper())
        return None if record is None else dict(record)

    def activate(self, stock: object) -> bool:
        if not isinstance(stock, ValidationStockRef):
            raise TypeError("stock must be ValidationStockRef")
        selected = _normalized_identity(stock.code, stock.name)
        if selected is None:
            raise ValueError("stock must contain code and name")
        record = self._records_by_code.get(selected.code)
        if record is not None:
            selected = ValidationStockRef(selected.code, str(record["name"]))
        updated = [selected]
        updated.extend(
            candidate
            for candidate in self._recent_stocks
            if candidate.code != selected.code
        )
        normalized = tuple(updated)
        if normalized == self._recent_stocks:
            return False
        self._recent_stocks = normalized
        self._write_recent_stocks()
        return True

    def retain_prefix(self, stocks: object) -> bool:
        source = stocks if isinstance(stocks, (list, tuple)) else ()
        retained: list[ValidationStockRef] = []
        for stock in source:
            if not isinstance(stock, ValidationStockRef):
                return False
            retained.append(ValidationStockRef(stock.code, stock.name))
        normalized = tuple(retained)
        if normalized != self._recent_stocks[:len(normalized)]:
            return False
        if normalized == self._recent_stocks:
            return False
        self._recent_stocks = normalized
        self._write_recent_stocks()
        return True

    def _write_recent_stocks(self) -> None:
        payload = [
            {"code": stock.code, "name": stock.name}
            for stock in self._recent_stocks
        ]
        try:
            self._settings.setValue(
                RECENT_STOCKS_SETTINGS_KEY,
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            self._settings.sync()
        except Exception:
            return
