from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication, QWidget

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
import gui_auto_trade_setting_window as setting_window
from kiwoom_api import KiwoomApi
import gui_windows
from routine_delete_policy import (
    DELETE_BLOCKED_LONG_TERM_HOLDING,
    DELETE_BLOCKED_OPERATION_RUNNING,
    preview_delete_scope,
)
from routine_instance_deletion_service import (
    RoutineInstanceDeletionScope,
    delete_routine_instance_completely,
)
from stock_assignment_registration_service import register_unassigned_stock_to_instance
from stock_repository import StockRecord, StockRepository


INSTANCE_A = "11111111-1111-4111-8111-111111111111"
INSTANCE_B = "22222222-2222-4222-8222-222222222222"
GROUP_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _stock(root: Path, *, holding: int = 0, review: bool = False) -> tuple[StockRecord, Path]:
    stock_dir = root / "stocks" / "005930_삼성전자"
    _write_json(
        stock_dir / "config.json",
        {
            "assigned_routine_instance_id": INSTANCE_A,
            "routine_instance_name": "루틴A",
            "routine_definition_id": "definition-a",
            "routine_type": "전략A",
            "routines": ["전략A"],
        },
    )
    state = {"status": "REVIEW_REQUIRED" if review else "STOPPED", "holding_qty": holding}
    if review:
        state["review_required"] = True
    _write_json(stock_dir / "state.json", state)
    _write_json(stock_dir / "orders.json", [])
    return (
        StockRecord(
            "005930",
            "삼성전자",
            "전략A",
            True,
            str(stock_dir.relative_to(root)),
            INSTANCE_A,
            "루틴A",
            "definition-a",
            "전략A",
        ),
        stock_dir,
    )


def _instance(root: Path) -> tuple[Path, SimpleNamespace]:
    instance_dir = root / "routine_instances" / INSTANCE_A
    payload = {
        "schema_version": "1.0",
        "instance_id": INSTANCE_A,
        "definition_id": "definition-a",
        "display_name": "루틴A",
        "description": "",
        "enabled": False,
        "rules_file": "rules.json",
        "created_at": "2026-08-23T09:00:00+09:00",
        "updated_at": "2026-08-23T09:00:00+09:00",
        "group_id": GROUP_ID,
    }
    _write_json(instance_dir / "instance.json", payload)
    _write_json(instance_dir / "rules.json", {})
    return instance_dir, SimpleNamespace(**payload, persisted=True)


class PreInitializationSafetyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_delete_guard_running_and_long_term_holding(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock, stock_dir = _stock(root)
            running = preview_delete_scope(root, [stock], running_stock_dirs=[stock_dir])
            self.assertEqual(DELETE_BLOCKED_OPERATION_RUNNING, running[0].reason_code)

            _write_json(stock_dir / "state.json", {"status": "STOPPED", "holding_qty": 3})
            holding = preview_delete_scope(root, [stock])
            self.assertEqual(DELETE_BLOCKED_LONG_TERM_HOLDING, holding[0].reason_code)

            _write_json(
                stock_dir / "state.json",
                {"status": "REVIEW_REQUIRED", "holding_qty": 3, "review_required": True},
            )
            self.assertEqual((), preview_delete_scope(root, [stock]))

    def test_instance_delete_unassigns_review_holding_and_preserves_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock, stock_dir = _stock(root, holding=7, review=True)
            instance_dir, instance = _instance(root)
            scope = RoutineInstanceDeletionScope(
                root, INSTANCE_A, "루틴A", GROUP_ID, "definition-a", instance_dir, (stock,)
            )
            state_before = (stock_dir / "state.json").read_bytes()
            result = delete_routine_instance_completely(scope)
            self.assertTrue(result.success, result.error)
            self.assertFalse(instance_dir.exists())
            config = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual("", config["assigned_routine_instance_id"])
            self.assertEqual(state_before, (stock_dir / "state.json").read_bytes())
            episodes = CanonicalAssignmentEpisodeRepository(root).list_episodes("005930")
            self.assertEqual("UNASSIGNED", episodes[-1].ownership_kind)
            self.assertTrue(any(item.ownership_kind == "ASSIGNED" for item in episodes))

    def test_instance_delete_failure_rolls_back_assignment_and_episode(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock, stock_dir = _stock(root)
            instance_dir, _instance_record = _instance(root)
            scope = RoutineInstanceDeletionScope(
                root, INSTANCE_A, "루틴A", GROUP_ID, "definition-a", instance_dir, (stock,)
            )
            config_before = (stock_dir / "config.json").read_bytes()
            episode_repository = CanonicalAssignmentEpisodeRepository(root)
            opened = episode_repository.open_episode(
                "005930",
                AssignmentEpisodeTarget.assigned(
                    instance_id=INSTANCE_A,
                    group_id=GROUP_ID,
                    definition_id="definition-a",
                    instance_name_snapshot="루틴A",
                    group_name_snapshot="그룹A",
                ),
                started_at="2026-08-23T09:00:00+09:00",
                start_reason="TEST_CURRENT",
                source="TEST",
            )
            self.assertTrue(opened.success)
            episode_path = episode_repository.document_path("005930")
            episode_before = episode_path.read_bytes()
            import routine_instance_deletion_service as instance_deletion

            real_rmtree = instance_deletion.shutil.rmtree
            calls = 0

            def fail_once(path, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("injected failure")
                return real_rmtree(path, *args, **kwargs)

            with patch.object(instance_deletion.shutil, "rmtree", side_effect=fail_once):
                result = delete_routine_instance_completely(scope)
            self.assertFalse(result.success)
            self.assertTrue(instance_dir.exists())
            self.assertEqual(config_before, (stock_dir / "config.json").read_bytes())
            self.assertEqual(episode_before, episode_path.read_bytes())
            current = episode_repository.get_open_episode("005930")
            self.assertIsNotNone(current)
            self.assertEqual("ASSIGNED", current.ownership_kind)

    def test_registration_is_idempotent_and_blocks_other_current_instance(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            _stock_record, stock_dir = _stock(root)
            config_path = stock_dir / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["assigned_routine_instance_id"] = ""
            config["routines"] = []
            _write_json(config_path, config)
            instance = SimpleNamespace(
                instance_id=INSTANCE_A,
                group_id=GROUP_ID,
                definition_id="definition-a",
                display_name="루틴A",
            )
            group = SimpleNamespace(group_id=GROUP_ID, display_name="그룹A")
            with (
                patch("assignment_episode_linkage.RoutineInstanceRepository.get_instance", return_value=instance),
                patch("assignment_episode_linkage.scan_group_records", return_value=[group]),
                patch("stock_repository._append_routine_changed"),
            ):
                first = register_unassigned_stock_to_instance(
                    root, "005930", "삼성전자",
                    instance_id=INSTANCE_A, instance_name="루틴A",
                    definition_id="definition-a", routine_type="전략A",
                )
                episode_bytes = CanonicalAssignmentEpisodeRepository(root).document_path("005930").read_bytes()
                second = register_unassigned_stock_to_instance(
                    root, "005930", "삼성전자",
                    instance_id=INSTANCE_A, instance_name="루틴A",
                    definition_id="definition-a", routine_type="전략A",
                )
                blocked = register_unassigned_stock_to_instance(
                    root, "005930", "삼성전자",
                    instance_id=INSTANCE_B, instance_name="루틴B",
                    definition_id="definition-a", routine_type="전략A",
                )
            self.assertTrue(first.success and first.changed)
            self.assertEqual("ALREADY_CURRENT", second.status)
            self.assertFalse(second.changed)
            self.assertEqual("CURRENT_ASSIGNED_ELSEWHERE", blocked.status)
            self.assertEqual(episode_bytes, CanonicalAssignmentEpisodeRepository(root).document_path("005930").read_bytes())

    def test_dialog_registry_is_process_wide_and_reopens_after_close(self) -> None:
        owner_a, owner_b = QWidget(), QWidget()
        self.addCleanup(owner_a.close)
        self.addCleanup(owner_b.close)
        from gui_stock_data import StockLibraryLoadSnapshot

        snapshot = StockLibraryLoadSnapshot("READY", "RUNTIME_LIBRARY", ())
        with patch.object(setting_window, "load_stock_library_snapshot", return_value=snapshot):
            first = setting_window.open_instance_stock_search_register_dialog(
                owner_a, {"instance_id": INSTANCE_A, "instance_name": "A"}
            )
            second = setting_window.open_instance_stock_search_register_dialog(
                owner_b, {"instance_id": INSTANCE_A, "instance_name": "A"}
            )
            other = setting_window.open_instance_stock_search_register_dialog(
                owner_b, {"instance_id": INSTANCE_B, "instance_name": "B"}
            )
            self.assertIs(first, second)
            self.assertIsNot(first, other)
            first.close()
            reopened = setting_window.open_instance_stock_search_register_dialog(
                owner_a, {"instance_id": INSTANCE_A, "instance_name": "A"}
            )
            self.assertIsNot(first, reopened)
            reopened.close()
            other.close()

    def test_login_single_flight_and_duplicate_success_callback(self) -> None:
        api = KiwoomApi.__new__(KiwoomApi)
        QObject.__init__(api)
        api._available = True
        api._unavailable_reason = ""
        api._login_requested = False
        api._login_session_id = ""
        api._connection_epoch = 0
        api._connected = False
        api.last_login_error = None
        api.last_login_message = ""
        api._control = MagicMock()
        api._control.dynamicCall.return_value = 0
        api._login_bootstrap_timer = MagicMock()
        api._login_bootstrap_desktop_probe_stop = MagicMock()
        api._login_bootstrap_desktop_probe_thread = None
        with (
            patch.object(api, "_prepare_login_bootstrap_observation"),
            patch.object(api, "_start_login_bootstrap_desktop_probe"),
            patch.object(api, "_stop_login_bootstrap_desktop_probe"),
            patch.object(api, "_stop_login_bootstrap_observation"),
            patch.object(api, "account_numbers", return_value=["12345678"]),
            patch.object(api, "clear_realtime_shadow_registration"),
        ):
            first = api.login()
            second = api.login()
            self.assertEqual("login_requested", first["status"])
            self.assertEqual("login_in_progress", second["status"])
            comm_connect_calls = [
                call for call in api._control.dynamicCall.call_args_list
                if call.args and call.args[0] == "CommConnect()"
            ]
            self.assertEqual(1, len(comm_connect_calls))
            api._on_event_connect(0)
            session_id = api._login_session_id
            epoch = api._connection_epoch
            api._on_event_connect(0)
            self.assertEqual(session_id, api._login_session_id)
            self.assertEqual(epoch, api._connection_epoch)
            self.assertEqual("already_connected", api.login()["status"])

    def test_login_failure_releases_single_flight_for_retry(self) -> None:
        api = KiwoomApi.__new__(KiwoomApi)
        QObject.__init__(api)
        api._available = True
        api._unavailable_reason = ""
        api._login_requested = False
        api._login_session_id = ""
        api._connection_epoch = 0
        api._connected = False
        api.last_login_error = None
        api.last_login_message = ""
        api._control = MagicMock()
        api._control.dynamicCall.side_effect = [-1, 0, 0]
        api._login_bootstrap_timer = MagicMock()
        api._login_bootstrap_desktop_probe_stop = MagicMock()
        api._login_bootstrap_desktop_probe_thread = None
        with (
            patch.object(api, "_prepare_login_bootstrap_observation"),
            patch.object(api, "_start_login_bootstrap_desktop_probe"),
            patch.object(api, "_stop_login_bootstrap_desktop_probe"),
            patch.object(api, "_stop_login_bootstrap_observation"),
        ):
            failed = api.login()
            self.assertEqual("login_request_failed", failed["status"])
            self.assertFalse(api._login_requested)
            retried = api.login()
            self.assertEqual("login_requested", retried["status"])
            comm_connect_calls = [
                call for call in api._control.dynamicCall.call_args_list
                if call.args and call.args[0] == "CommConnect()"
            ]
            self.assertEqual(2, len(comm_connect_calls))

    def test_duplicate_login_success_runs_main_followups_once(self) -> None:
        status_bar = MagicMock()
        operation_host = MagicMock()
        window = SimpleNamespace(
            _event_journal_kiwoom_connected=False,
            _handled_kiwoom_login_identity=None,
            _apply_connected_kiwoom_login_button_state=MagicMock(),
            _apply_kiwoom_login_button_state=MagicMock(),
            login_status_label=MagicMock(),
            refresh_kiwoom_accounts=MagicMock(),
            sync_account_funds_selection=MagicMock(),
            request_account_funds=MagicMock(),
            start_production_recovery=MagicMock(),
            start_stock_library_sync_for_current_session=MagicMock(),
            main_monitoring_auto_trade_operation_host=MagicMock(
                return_value=operation_host
            ),
            statusBar=MagicMock(return_value=status_bar),
        )
        payload = {
            "connected": True,
            "message": "login succeeded",
            "connection_epoch": 3,
            "login_session_id": "SESSION-3",
        }
        with (
            patch.object(gui_windows.QTimer, "singleShot") as single_shot,
            patch.object(gui_windows, "append_production_event") as event,
        ):
            gui_windows.MainWindow.on_kiwoom_login_state_changed(window, payload)
            gui_windows.MainWindow.on_kiwoom_login_state_changed(window, payload)
        window.request_account_funds.assert_called_once_with()
        window.start_production_recovery.assert_called_once_with()
        operation_host.sync_monitoring_universe_for_current_session.assert_called_once_with()
        single_shot.assert_called_once()
        event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
