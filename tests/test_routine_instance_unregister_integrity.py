from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

import gui_auto_trade_setting_window as setting_window
from mock_validation_contract import initial_session_document
from mock_validation_reference_snapshot import build_mock_reference_snapshot
from mock_validation_repository import MockValidationRepository
from mock_validation_ui_actions import MockValidationUIActions
from routine_instance_deletion_service import (
    ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS,
    ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS,
    RoutineInstanceDeletionResult,
    RoutineInstanceDeletionScope,
    RoutineInstanceMockRegistration,
    current_mock_registrations_for_routine_instance,
    delete_routine_instance_completely,
)
from routine_instance_repository import RoutineInstanceRepository


INSTANCE_A = "11111111-1111-4111-8111-111111111111"
INSTANCE_B = "22222222-2222-4222-8222-222222222222"
GROUP_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ASSIGNED_BLOCK_TOAST = (
    "해당루틴은 삭제 불가합니다.\n"
    "등록된 종목을 모두 해제하세요."
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _instance_scope(root: Path) -> RoutineInstanceDeletionScope:
    instance_dir = root / "routine_instances" / INSTANCE_A
    _write_json(instance_dir / "instance.json", {"instance_id": INSTANCE_A})
    _write_json(instance_dir / "rules.json", {})
    return RoutineInstanceDeletionScope(
        root,
        INSTANCE_A,
        "루틴A",
        GROUP_ID,
        "indicator_follow",
        instance_dir,
        (),
    )


def _mock_document(
    stock_code: str,
    instance_ids: tuple[str, ...],
    *,
    session_id: str,
) -> dict[str, object]:
    snapshot = build_mock_reference_snapshot(
        stock={
            "code": stock_code,
            "name": f"종목{stock_code}",
            "stock_path": f"stocks/{stock_code}_종목",
        },
        routine_instances=[
            {
                "instance_id": instance_id,
                "definition_id": "indicator_follow",
                "routine_type": "INDICATOR_FOLLOW",
                "display_name": f"루틴{instance_id[-1]}",
            }
            for instance_id in instance_ids
        ],
        rules_by_instance_id={instance_id: {} for instance_id in instance_ids},
        created_at="2026-09-08T09:00:00+09:00",
    )
    return initial_session_document(
        validation_session_id=session_id,
        reference_snapshot=snapshot,
        created_at="2026-09-08T09:00:00+09:00",
    )


def _registration(
    stock_code: str,
    instance_ids: tuple[str, ...],
    *,
    session_id: str,
    active: bool = False,
) -> RoutineInstanceMockRegistration:
    document = _mock_document(stock_code, instance_ids, session_id=session_id)
    if active:
        document["mock_operation_lifecycle"] = {
            "version": 1,
            "current": {"state": "RUNNING"},
            "history": [],
            "commands": {},
            "instance_operations": {},
        }
    return RoutineInstanceMockRegistration(
        validation_session_id=session_id,
        stock_code=stock_code,
        routine_instance_ids=instance_ids,
        document=document,
    )


class RoutineInstanceUnregisterIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_backend_blocks_multiple_assigned_stocks_without_cascade(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            scope = _instance_scope(root)
            configs: list[Path] = []
            for code in ("005930", "000660"):
                path = root / "stocks" / f"{code}_종목" / "config.json"
                _write_json(
                    path,
                    {
                        "code": code,
                        "name": f"종목{code}",
                        "routines": ["루틴A"],
                        "assigned_routine_instance_id": INSTANCE_A,
                    },
                )
                configs.append(path)
            before = {path: path.read_bytes() for path in configs}

            result = delete_routine_instance_completely(scope)

            self.assertFalse(result.success)
            self.assertEqual(ROUTINE_INSTANCE_DELETE_ASSIGNED_STOCKS, result.reason_code)
            self.assertEqual(2, result.assigned_stock_count)
            self.assertTrue(scope.instance_dir.exists())
            self.assertEqual(before, {path: path.read_bytes() for path in configs})

    def test_backend_blocks_current_mock_registration(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            scope = _instance_scope(root)
            repository = MockValidationRepository(
                root / "mock_validation",
                project_root=root,
            )
            repository.create_session(
                _mock_document(
                    "005930",
                    (INSTANCE_A,),
                    session_id="MV-backend-mock-a",
                )
            )

            result = delete_routine_instance_completely(scope)

            self.assertFalse(result.success)
            self.assertEqual(ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS, result.reason_code)
            self.assertEqual(1, result.mock_registration_count)
            self.assertTrue(scope.instance_dir.exists())
            self.assertEqual("MV-backend-mock-a", repository.current_session_id("005930"))

    def test_mock_target_query_uses_instance_identity_only(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            repository = MockValidationRepository(
                root / "mock_validation",
                project_root=root,
            )
            repository.create_session(
                _mock_document(
                    "005930",
                    (INSTANCE_A,),
                    session_id="MV-target-a",
                )
            )
            repository.create_session(
                _mock_document(
                    "000660",
                    (INSTANCE_B,),
                    session_id="MV-nontarget-b",
                )
            )

            matches = current_mock_registrations_for_routine_instance(
                root,
                INSTANCE_A,
            )

            self.assertEqual(("005930",), tuple(item.stock_code for item in matches))
            self.assertEqual("MV-nontarget-b", repository.current_session_id("000660"))

    def test_direct_repository_writer_blocks_current_mock_registration(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            scope = _instance_scope(root)
            repository = RoutineInstanceRepository(root)
            with (
                patch.object(
                    repository,
                    "get_instance",
                    return_value=SimpleNamespace(instance_id=INSTANCE_A),
                ),
                patch(
                    "routine_instance_deletion_service.current_mock_registrations_for_routine_instance",
                    return_value=(
                        _registration(
                            "005930",
                            (INSTANCE_A,),
                            session_id="MV-direct-writer-a",
                        ),
                    ),
                ),
            ):
                result = repository.delete_instance(INSTANCE_A)

            self.assertFalse(result.success)
            self.assertEqual(
                ROUTINE_INSTANCE_DELETE_MOCK_REGISTRATIONS,
                result.error_code,
            )
            self.assertEqual(1, result.mock_registration_count)
            self.assertTrue(scope.instance_dir.exists())

    def test_zero_dependencies_deletes_instance_and_preserves_group(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            scope = _instance_scope(root)
            group_path = root / "groups" / GROUP_ID / "group.json"
            _write_json(group_path, {"group_id": GROUP_ID})
            group_before = group_path.read_bytes()

            result = delete_routine_instance_completely(scope)

            self.assertTrue(result.success, result.error)
            self.assertFalse(scope.instance_dir.exists())
            self.assertEqual(group_before, group_path.read_bytes())

    def test_ui_assigned_stock_blocks_before_confirmation_and_mock_preflight(self) -> None:
        for stock_codes in (("005930",), ("005930", "000660")):
            with self.subTest(stock_codes=stock_codes):
                window = QWidget()
                scope = SimpleNamespace(
                    stocks=tuple(SimpleNamespace(code=code) for code in stock_codes)
                )
                with (
                    patch(
                        "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                        return_value=scope,
                    ),
                    patch(
                        "routine_instance_deletion_service.current_mock_registrations_for_routine_instance"
                    ) as mock_preflight,
                    patch(
                        "routine_instance_deletion_service.delete_routine_instance_completely"
                    ) as delete_routine,
                    patch.object(setting_window, "show_toast") as toast,
                    patch.object(setting_window.QMessageBox, "question") as question,
                ):
                    setting_window.delete_routine_instance_with_existing_policy(
                        window,
                        {
                            "row_kind": "instance",
                            "instance_id": INSTANCE_A,
                            "instance_name": "루틴A",
                        },
                    )

                toast.assert_called_once_with(window, ASSIGNED_BLOCK_TOAST)
                mock_preflight.assert_not_called()
                delete_routine.assert_not_called()
                question.assert_not_called()
                window.deleteLater()

    def test_ui_without_assigned_stock_asks_before_mock_preflight(self) -> None:
        window = QWidget()
        with (
            patch(
                "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                return_value=SimpleNamespace(stocks=()),
            ),
            patch(
                "routine_instance_deletion_service.current_mock_registrations_for_routine_instance"
            ) as mock_preflight,
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=QMessageBox.No,
            ) as question,
        ):
            setting_window.delete_routine_instance_with_existing_policy(
                window,
                {
                    "row_kind": "instance",
                    "instance_id": INSTANCE_A,
                    "instance_name": "루틴A",
                },
            )

        question.assert_called_once_with(
            window,
            "등록삭제",
            "'루틴A' 루틴 등록을 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        mock_preflight.assert_not_called()
        window.deleteLater()

    def test_mock_preflight_is_atomic_when_any_target_is_active(self) -> None:
        window = QWidget()
        window.mock_validation_ui_actions = MagicMock()
        registrations = (
            _registration("005930", (INSTANCE_A,), session_id="MV-ready-a"),
            _registration(
                "000660",
                (INSTANCE_A,),
                session_id="MV-active-a",
                active=True,
            ),
        )
        with (
            patch(
                "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                return_value=SimpleNamespace(stocks=()),
            ),
            patch(
                "routine_instance_deletion_service.current_mock_registrations_for_routine_instance",
                return_value=registrations,
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ) as question,
        ):
            setting_window.delete_routine_instance_with_existing_policy(
                window,
                {
                    "row_kind": "instance",
                    "instance_id": INSTANCE_A,
                    "instance_name": "루틴A",
                },
            )

        warning.assert_called_once()
        question.assert_called_once()
        window.mock_validation_ui_actions.unregister.assert_not_called()
        window.deleteLater()

    def test_shared_mock_session_blocks_without_removing_any_registration(self) -> None:
        window = QWidget()
        window.mock_validation_ui_actions = MagicMock()
        registrations = (
            _registration(
                "005930",
                (INSTANCE_A, INSTANCE_B),
                session_id="MV-shared-ab",
            ),
        )
        with (
            patch(
                "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                return_value=SimpleNamespace(stocks=()),
            ),
            patch(
                "routine_instance_deletion_service.current_mock_registrations_for_routine_instance",
                return_value=registrations,
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ) as question,
        ):
            setting_window.delete_routine_instance_with_existing_policy(
                window,
                {
                    "row_kind": "instance",
                    "instance_id": INSTANCE_A,
                    "instance_name": "루틴A",
                },
            )

        warning.assert_called_once()
        question.assert_called_once()
        window.mock_validation_ui_actions.unregister.assert_not_called()
        window.deleteLater()

    def test_eligible_target_mock_registrations_are_removed_before_routine(self) -> None:
        window = QWidget()
        window.mock_validation_ui_actions = MagicMock()
        window.mock_validation_ui_actions.unregister.return_value = {
            "ok": True,
            "purge": {"events_remaining": 0},
        }
        registrations = (
            _registration("005930", (INSTANCE_A,), session_id="MV-only-a-1"),
            _registration("000660", (INSTANCE_A,), session_id="MV-only-a-2"),
        )
        deletion_result = RoutineInstanceDeletionResult(True)
        with (
            patch(
                "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                return_value=SimpleNamespace(stocks=()),
            ),
            patch(
                "routine_instance_deletion_service.current_mock_registrations_for_routine_instance",
                side_effect=(registrations, ()),
            ),
            patch(
                "routine_instance_deletion_service.delete_routine_instance_completely",
                return_value=deletion_result,
            ) as delete_routine,
            patch.object(
                setting_window,
                "auto_trade_running_registered_operation_targets",
                return_value=[],
            ),
            patch.object(setting_window.QMessageBox, "question", return_value=QMessageBox.Yes),
            patch.object(setting_window, "show_toast") as toast,
            patch.object(setting_window, "append_production_event"),
            patch.object(setting_window, "refresh_auto_trade_views"),
        ):
            setting_window.delete_routine_instance_with_existing_policy(
                window,
                {
                    "row_kind": "instance",
                    "instance_id": INSTANCE_A,
                    "instance_name": "루틴A",
                },
            )

        self.assertEqual(
            [
                call(
                    "005930",
                    expected_validation_session_id="MV-only-a-1",
                ),
                call(
                    "000660",
                    expected_validation_session_id="MV-only-a-2",
                ),
            ],
            window.mock_validation_ui_actions.unregister.call_args_list,
        )
        toast.assert_called_once_with(window, "모의검증에 등록상태도 삭제 합니다.")
        delete_routine.assert_called_once()
        window.deleteLater()

    def test_mock_unregister_failure_never_deletes_routine(self) -> None:
        window = QWidget()
        window.mock_validation_ui_actions = MagicMock()
        window.mock_validation_ui_actions.unregister.return_value = {
            "ok": False,
            "reason": "MOCK_OPERATION_ACTIVE",
        }
        registrations = (
            _registration("005930", (INSTANCE_A,), session_id="MV-only-a"),
        )
        with (
            patch(
                "routine_instance_deletion_service.collect_routine_instance_deletion_scope",
                return_value=SimpleNamespace(stocks=()),
            ),
            patch(
                "routine_instance_deletion_service.current_mock_registrations_for_routine_instance",
                return_value=registrations,
            ),
            patch(
                "routine_instance_deletion_service.delete_routine_instance_completely"
            ) as delete_routine,
            patch.object(
                setting_window.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ),
            patch.object(setting_window.QMessageBox, "warning") as warning,
            patch.object(setting_window, "show_toast"),
        ):
            setting_window.delete_routine_instance_with_existing_policy(
                window,
                {
                    "row_kind": "instance",
                    "instance_id": INSTANCE_A,
                    "instance_name": "루틴A",
                },
            )

        warning.assert_called_once()
        delete_routine.assert_not_called()
        window.deleteLater()

    def test_mock_unregister_expected_session_identity_is_fail_closed(self) -> None:
        document = _mock_document(
            "005930",
            (INSTANCE_A,),
            session_id="MV-current-session",
        )
        host = SimpleNamespace(
            repository=MagicMock(),
            session_service=MagicMock(),
            current_session=MagicMock(return_value=document),
        )
        actions = MockValidationUIActions(host)

        result = actions.unregister(
            "005930",
            expected_validation_session_id="MV-stale-session",
        )

        self.assertFalse(result["ok"])
        self.assertEqual("MOCK_CONTEXT_TARGET_STALE", result["reason"])
        host.session_service.record_unregister_event.assert_not_called()
        host.session_service.end_stock_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
