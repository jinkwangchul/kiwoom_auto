from __future__ import annotations

from contextlib import ExitStack
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable
import unittest
from unittest import mock

from tests.production_mutable_guard import install, installed


install()


class TemporaryProjectRoot:
    """Test-owned project layout for mutable runtime and domain fixtures."""

    def __init__(self, *, prefix: str = "kiwoom_auto_test_root_") -> None:
        self._temporary = TemporaryDirectory(prefix=prefix)
        self.root = Path(self._temporary.name).resolve()
        self.runtime = self.root / "runtime"
        self.stocks = self.root / "stocks"
        self.routines = self.root / "routines"
        self.routine_instances = self.root / "routine_instances"
        self.groups = self.root / "groups"
        self.logs = self.root / "logs"
        self.reports = self.root / "reports"
        for directory in (
            self.runtime,
            self.stocks,
            self.routines,
            self.routine_instances,
            self.groups,
            self.logs,
            self.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> "TemporaryProjectRoot":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.cleanup()


def write_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def create_stock_fixture(
    layout: TemporaryProjectRoot,
    *,
    code: str = "005930",
    name: str = "삼성전자",
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    orders: list[dict[str, Any]] | None = None,
) -> Path:
    stock_dir = layout.stocks / f"{code}_{name}"
    write_json(
        stock_dir / "config.json",
        {
            "code": code,
            "name": name,
            "operation_excluded": False,
            "real_trade_enabled": False,
            "assigned_routine_instance_id": "",
            **dict(config or {}),
        },
    )
    write_json(
        stock_dir / "state.json",
        {
            "status": "STOPPED",
            "trade_enabled": False,
            "trade_started": False,
            "holding_qty": 0,
            "holding_amount": 0,
            "avg_price": 0,
            "pending_order": False,
            "pending_qty": 0,
            **dict(state or {}),
        },
    )
    write_json(stock_dir / "orders.json", list(orders or []))
    return stock_dir


def _under_root(path_value: Any, root: Path) -> bool:
    try:
        Path(path_value).resolve(strict=False).relative_to(root.resolve())
    except (OSError, TypeError, ValueError):
        return False
    return True


def patch_project_runtime_classifiers(
    runtime_root: Path,
    module_names: Iterable[str],
) -> ExitStack:
    """Point existing private project-runtime classifiers at a test root."""

    runtime_root = Path(runtime_root).resolve()
    stack = ExitStack()
    for module_name in module_names:
        module = importlib.import_module(module_name)
        if hasattr(module, "_project_runtime_root"):
            stack.enter_context(
                mock.patch.object(
                    module,
                    "_project_runtime_root",
                    return_value=runtime_root,
                )
            )
            continue
        if hasattr(module, "_under_project_runtime"):
            stack.enter_context(
                mock.patch.object(
                    module,
                    "_under_project_runtime",
                    side_effect=lambda path, root=runtime_root: _under_root(path, root),
                )
            )
            continue
        stack.close()
        raise AttributeError(
            f"{module_name} has no project-runtime classifier seam"
        )
    return stack


def assert_project_mutable_guard_active(test_case: unittest.TestCase) -> None:
    test_case.assertTrue(
        installed(),
        "Production mutable guard must be installed before this test runs",
    )
