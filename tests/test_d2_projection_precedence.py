import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QTableWidget

import gui_auto_trade_setting_window as setting_window
import gui_main_table_loader
import gui_review_required_window
from gui_auto_trade_integrity import (
    REVIEW_REASON_OPERATION_DATA_MISSING,
)
from gui_auto_trade_policy import auto_trade_stock_operation_category
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from tests.participant_owner_fixture import participant_owner


class D2ProjectionPrecedenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _main_review_count(stock_dir: Path) -> int:
        counts = gui_main_table_loader._instance_stock_counts(
            window=SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner(),
            ),
            static_data={
                "stocks": (
                    {
                        "stock_dir": str(stock_dir),
                        "stock_path": f"stocks/{stock_dir.name}",
                        "instance_id": "instance-a",
                        "operation_excluded": False,
                        "code": "000001",
                        "name": "테스트",
                        "enabled": True,
                    },
                ),
            },
        )
        return int(counts["instance-a"]["review"])

    @staticmethod
    def _settings_review(stock_dir: Path) -> tuple[bool, str]:
        repository = SimpleNamespace(resolve_stock_dir=lambda _code, _name: stock_dir)
        with patch.object(setting_window, "StockRepository", return_value=repository):
            owner = SimpleNamespace()
            required = AutoTradeSettingWindow._routine_tree_stock_is_review_required(
                owner,
                "000001",
                "테스트",
            )
        return required, "검토관리" if required else "정상"

    def test_missing_and_corrupt_state_are_review_in_all_three_projections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000001_테스트"
            stock_dir.mkdir()

            for payload in (
                None,
                "{broken",
                json.dumps({"status": "STOPPED", "holding_qty": "invalid"}),
            ):
                state_path = stock_dir / "state.json"
                if payload is None:
                    state_path.unlink(missing_ok=True)
                else:
                    state_path.write_text(payload, encoding="utf-8")

                settings_required, settings_classification = self._settings_review(
                    stock_dir
                )
                self.assertEqual(1, self._main_review_count(stock_dir))
                self.assertTrue(settings_required)
                self.assertEqual("검토관리", settings_classification)
                self.assertTrue(
                    gui_review_required_window.is_review_required_stock_dir(stock_dir)
                )

    def test_preloaded_missing_state_issue_matches_file_inspector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000001_테스트"
            stock_dir.mkdir()
            counts = gui_main_table_loader._instance_stock_counts(
                window=SimpleNamespace(
                    _main_monitoring_auto_trade_operation_host=participant_owner(),
                ),
                static_data={
                    "stocks": (
                        {
                            "stock_dir": str(stock_dir),
                            "stock_path": f"stocks/{stock_dir.name}",
                            "instance_id": "instance-a",
                            "operation_excluded": False,
                            "code": "000001",
                            "name": "테스트",
                        },
                    ),
                },
                state_by_stock_dir={str(stock_dir): {}},
                state_issue_by_stock_dir={
                    str(stock_dir): REVIEW_REASON_OPERATION_DATA_MISSING,
                },
            )
            self.assertEqual(1, counts["instance-a"]["review"])

    def test_settings_refresh_does_not_call_realtrade_writer_or_change_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "config.json"
            state_path = Path(temp) / "state.json"
            config_path.write_text(
                json.dumps({"real_trade_enabled": False}),
                encoding="utf-8",
            )
            state_path.write_text(json.dumps({"status": "STOPPED"}), encoding="utf-8")
            before_bytes = (config_path.read_bytes(), state_path.read_bytes())
            before_mtime = (
                config_path.stat().st_mtime_ns,
                state_path.stat().st_mtime_ns,
            )

            table = QTableWidget(0, 1)
            owner = SimpleNamespace(
                routine_table=table,
                _initializing_open_refresh=False,
                _all_stocks_scope_active=False,
                capture_stock_table_view_state=lambda: (set(), 0),
                current_selected_routine_row_metadata=lambda: None,
                load_routine_table=MagicMock(),
                restore_routine_selection_metadata=MagicMock(),
                update_selected_routine_status_bar=MagicMock(),
                load_selected_routine_stocks=MagicMock(),
                restore_stock_table_view_state=MagicMock(),
                update_review_required_button_text=MagicMock(),
                update_action_buttons=MagicMock(),
            )
            with patch.object(
                setting_window,
                "normalize_base_stock_single_routine_file",
                return_value=False,
            ), patch.object(
                setting_window,
                "ensure_single_real_trade_routine_for_all_stocks",
            ) as realtrade_writer:
                AutoTradeSettingWindow.refresh_all(owner)

            self.assertEqual(
                before_bytes,
                (config_path.read_bytes(), state_path.read_bytes()),
            )
            self.assertEqual(
                before_mtime,
                (config_path.stat().st_mtime_ns, state_path.stat().st_mtime_ns),
            )
            realtrade_writer.assert_not_called()

    def test_operation_projection_keeps_review_exclusion_current_session_precedence(self) -> None:
        participant_window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner({"000001"}),
        )
        nonparticipant_window = SimpleNamespace(
            _main_monitoring_auto_trade_operation_host=participant_owner(),
        )
        self.assertEqual(
            "review",
            auto_trade_stock_operation_category(
                participant_window,
                stock_code="000001",
                persisted_trade_started=True,
                operation_excluded=True,
                review_required=True,
            ),
        )
        self.assertEqual(
            "excluded",
            auto_trade_stock_operation_category(
                participant_window,
                stock_code="000001",
                persisted_trade_started=True,
                operation_excluded=True,
                review_required=False,
            ),
        )
        self.assertEqual(
            "operation",
            auto_trade_stock_operation_category(
                participant_window,
                stock_code="000001",
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            ),
        )
        self.assertEqual(
            "waiting",
            auto_trade_stock_operation_category(
                nonparticipant_window,
                stock_code="000001",
                persisted_trade_started=True,
                operation_excluded=False,
                review_required=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
