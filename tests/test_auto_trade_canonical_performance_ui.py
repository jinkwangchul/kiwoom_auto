from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from auto_trade_performance_ui import build_canonical_performance_ui_snapshot
from gui_auto_trade_setting_window import AutoTradeSettingWindow
from performance_ledger_repository import (
    CANONICAL_OWNER_POLICY,
    OWNERSHIP_UNRESOLVED,
    CanonicalStockPerformanceLedgerRepository,
)
from performance_metrics import MetricStatus


class AutoTradeCanonicalPerformanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.episodes = CanonicalAssignmentEpisodeRepository(self.root)
        self.ledger = CanonicalStockPerformanceLedgerRepository(
            self.root,
            episode_repository=self.episodes,
            now_factory=lambda: datetime(
                2026,
                8,
                23,
                15,
                0,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )
        self.sequence = 0

    @staticmethod
    def target(instance_id: str, group_id: str, instance_name: str, group_name: str):
        return AssignmentEpisodeTarget.assigned(
            instance_id=instance_id,
            group_id=group_id,
            definition_id="indicator_follow",
            instance_name_snapshot=instance_name,
            group_name_snapshot=group_name,
        )

    def open_unassigned(self, code: str, at: str):
        result = self.episodes.open_episode(
            code,
            AssignmentEpisodeTarget.unassigned(),
            started_at=at,
            start_reason="STOCK_REGISTERED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)
        return result.opened_episode

    def transition(self, code: str, target, at: str):
        result = self.episodes.transition_episode(
            code,
            target,
            changed_at=at,
            start_reason="ASSIGNMENT_CHANGED",
            end_reason="ASSIGNMENT_CHANGED",
            source="TEST",
        )
        self.assertTrue(result.success, result.error)
        return result.opened_episode

    def append(
        self,
        code: str,
        episode_id: str,
        *,
        net: int | None,
        gross: int,
        cost: int,
        trade_date: str,
        owner_id: str | None = None,
    ) -> None:
        self.sequence += 1
        result = self.ledger.append_event(
            {
                "stock_code": code,
                "broker": "KIWOOM",
                "account_number": "12345678",
                "trade_date": trade_date,
                "broker_order_no": f"ORDER-{self.sequence}",
                "execution_identity": f"EXEC-{self.sequence}",
                "fill_id": f"FILL-{self.sequence}",
                "realization_id": f"REALIZED-{self.sequence}",
                "realized_at": f"{trade_date}T12:00:00+09:00",
                "quantity": 1,
                "realized_cost_basis": cost,
                "gross_pnl": gross,
                "fee": 0 if net is not None else None,
                "tax": 0 if net is not None else None,
                "net_pnl": net,
                "exit_episode_id": episode_id,
                "canonical_owner_policy": CANONICAL_OWNER_POLICY,
                "allocations": [
                    {
                        "entry_lot_id": f"LOT-{self.sequence}",
                        "entry_episode_id": owner_id or episode_id,
                        "quantity": 1,
                        "cost_basis": cost,
                        "gross_pnl": gross,
                        "net_pnl": net,
                    }
                ],
            }
        )
        self.assertTrue(result.success, result.error)

    def fixture(self):
        s1 = "005930"
        self.open_unassigned(s1, "2026-08-20T09:00:00+09:00")
        a1 = self.transition(
            s1,
            self.target("A", "G1", "Alpha Old", "Group One"),
            "2026-08-20T09:10:00+09:00",
        )
        b1 = self.transition(
            s1,
            self.target("B", "G1", "Beta", "Group One"),
            "2026-08-21T09:10:00+09:00",
        )
        a2 = self.transition(
            s1,
            self.target("A", "G2", "Alpha New", "Group Two"),
            "2026-08-22T09:10:00+09:00",
        )
        self.append(s1, a1.episode_id, net=100, gross=100, cost=1000, trade_date="2026-08-20")
        self.append(s1, b1.episode_id, net=50, gross=50, cost=500, trade_date="2026-08-21")
        self.append(s1, a2.episode_id, net=30, gross=30, cost=300, trade_date="2026-08-22")

        s2 = "000660"
        self.open_unassigned(s2, "2026-08-22T09:00:00+09:00")
        c1 = self.transition(
            s2,
            self.target("C", "G2", "Charlie", "Group Two"),
            "2026-08-22T09:10:00+09:00",
        )
        self.append(s2, c1.episode_id, net=20, gross=20, cost=200, trade_date="2026-08-22")

        s3 = "035420"
        self.append(
            s3,
            OWNERSHIP_UNRESOLVED,
            net=10,
            gross=10,
            cost=100,
            trade_date="2026-08-22",
            owner_id=OWNERSHIP_UNRESOLVED,
        )

        stocks = [
            SimpleNamespace(code=s1, name="Samsung", assigned_routine_instance_id="A"),
            SimpleNamespace(code=s2, name="SK", assigned_routine_instance_id="C"),
            SimpleNamespace(code=s3, name="NAVER", assigned_routine_instance_id=""),
        ]
        instances = [
            SimpleNamespace(instance_id="A", group_id="G2", display_name="Alpha Current"),
            SimpleNamespace(instance_id="B", group_id="G1", display_name="Beta"),
            SimpleNamespace(instance_id="C", group_id="G2", display_name="Charlie"),
        ]
        groups = [
            SimpleNamespace(group_id="G1", display_name="Group One"),
            SimpleNamespace(group_id="G2", display_name="Group Two"),
        ]
        return build_canonical_performance_ui_snapshot(
            self.root,
            stocks=stocks,
            instances=instances,
            groups=groups,
        )

    def test_scope_truth_table_stock_lifetime_and_parent_episode_ownership(self) -> None:
        snapshot = self.fixture()
        engine = snapshot.metrics

        for scope in ("all", "current"):
            stock = next(row for row in snapshot.stock_rows(scope) if row.stock_code == "005930")
            self.assertEqual(180, engine.calculate(stock.lifetime).profit_amount.value)
        self.assertNotIn(
            "005930",
            {row.stock_code for row in snapshot.stock_rows("historical")},
        )

        current_instances = {
            row.instance_id: engine.calculate(row.aggregate).profit_amount.value
            for row in snapshot.instance_rows("current")
        }
        past_instances = {
            row.instance_id: engine.calculate(row.aggregate).profit_amount.value
            for row in snapshot.instance_rows("historical")
        }
        all_instances = {
            row.instance_id: engine.calculate(row.aggregate).profit_amount.value
            for row in snapshot.instance_rows("all")
        }
        self.assertEqual({"A": 30, "C": 20}, current_instances)
        self.assertEqual({"A": 100, "B": 50}, past_instances)
        self.assertEqual({"A": 130, "B": 50, "C": 20}, all_instances)

        current_groups = {
            row.group_id: engine.calculate(row.aggregate).profit_amount.value
            for row in snapshot.group_rows("current")
        }
        past_groups = {
            row.group_id: engine.calculate(row.aggregate).profit_amount.value
            for row in snapshot.group_rows("historical")
        }
        self.assertEqual({"G2": 50}, current_groups)
        self.assertEqual({"G1": 150}, past_groups)
        self.assertEqual(2, snapshot.episode_document_reads)
        self.assertEqual(3, snapshot.ledger_document_reads)

    def test_deleted_parent_snapshot_and_group_move_do_not_follow_current_relation(self) -> None:
        code = "247540"
        self.open_unassigned(code, "2026-08-20T09:00:00+09:00")
        deleted = self.transition(
            code,
            self.target("DELETED-I", "DELETED-G", "Deleted Instance", "Deleted Group"),
            "2026-08-20T09:10:00+09:00",
        )
        self.transition(code, AssignmentEpisodeTarget.unassigned(), "2026-08-21T09:00:00+09:00")
        self.append(code, deleted.episode_id, net=40, gross=40, cost=400, trade_date="2026-08-20")
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="Eco", assigned_routine_instance_id="")],
            instances=[],
            groups=[],
        )

        instance = snapshot.instance_rows("historical")[0]
        group = snapshot.group_rows("historical")[0]
        self.assertEqual("Deleted Instance", snapshot.instance_name(instance))
        self.assertEqual("Deleted Group", snapshot.group_name(group))
        self.assertEqual(40, snapshot.metrics.calculate(instance.aggregate).profit_amount.value)
        self.assertEqual(40, snapshot.metrics.calculate(group.aggregate).profit_amount.value)

    def test_ui_payload_uses_canonical_status_values_including_valid_zero(self) -> None:
        snapshot = self.fixture()
        stock = next(row for row in snapshot.stock_rows("all") if row.stock_code == "005930")
        harness = SimpleNamespace(
            _canonical_performance_snapshot_error="",
        )
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )
        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            stock,
            snapshot,
        )
        self.assertEqual("+180", payload["performance_profit_amount"])
        self.assertEqual("+10.00%", payload["performance_profit_rate"])
        self.assertEqual("CANONICAL", payload["performance_source"])
        self.assertEqual(0, payload["performance_profit_sort_status_rank"])

        empty = build_canonical_performance_ui_snapshot(
            self.root / "empty",
            stocks=[SimpleNamespace(code="005930", name="Samsung", assigned_routine_instance_id="A")],
            instances=[SimpleNamespace(instance_id="A", group_id="G", display_name="A")],
            groups=[SimpleNamespace(group_id="G", display_name="G")],
        )
        empty_row = empty.stock_rows("current")[0]
        empty_payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            empty_row,
            empty,
        )
        self.assertEqual("0", empty_payload["performance_period_value"])
        self.assertEqual("0", empty_payload["performance_profit_amount"])
        self.assertEqual("0.00%", empty_payload["performance_profit_rate"])
        self.assertEqual("0", empty_payload["performance_average_amount"])
        self.assertEqual("0.00%", empty_payload["performance_average_rate"])
        self.assertEqual("0.0", empty_payload["performance_efficiency_value"])
        self.assertEqual("기간(0)", empty_payload["performance_period_text"])
        self.assertEqual("수익(0 / 0.00%)", empty_payload["performance_profit_text"])
        self.assertEqual("평균(0 / 0.00%)", empty_payload["performance_average_text"])
        self.assertEqual("효율(0.0)", empty_payload["performance_efficiency_text"])
        empty_metrics = empty.metric_result(empty_row)
        self.assertEqual(MetricStatus.UNDEFINED, empty_metrics.profit_rate.status)
        self.assertEqual(MetricStatus.UNDEFINED, empty_metrics.average_amount.status)
        self.assertEqual(MetricStatus.UNAVAILABLE, empty_metrics.average_rate.status)
        self.assertEqual(MetricStatus.UNAVAILABLE, empty_metrics.efficiency.status)
        self.assertIn("VALID_ZERO", empty_payload["performance_profit_tooltip"])
        self.assertIn("ZERO_REALIZED_COST_BASIS", empty_payload["performance_profit_tooltip"])
        self.assertEqual(0, empty_payload["performance_profit_sort_status_rank"])

        empty_parent = empty.instance_rows("current")[0]
        empty_parent_payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            empty_parent,
            empty,
        )
        self.assertEqual("기간(0)", empty_parent_payload["performance_period_text"])
        self.assertEqual("수익(0 / 0.00%)", empty_parent_payload["performance_profit_text"])
        self.assertEqual("평균(0 / 0.00%)", empty_parent_payload["performance_average_text"])
        self.assertEqual("효율(0.0)", empty_parent_payload["performance_efficiency_text"])
        self.assertIn(
            "NO_CANONICAL_PERFORMANCE_EVENTS",
            empty_parent_payload["performance_profit_tooltip"],
        )

        unavailable_payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            None,
            None,
        )
        self.assertEqual("기간(-)", unavailable_payload["performance_period_text"])
        self.assertEqual("수익(- / -)", unavailable_payload["performance_profit_text"])
        self.assertEqual("평균(- / -)", unavailable_payload["performance_average_text"])
        self.assertEqual("효율(-)", unavailable_payload["performance_efficiency_text"])

    def test_zero_value_event_is_rendered_as_zero(self) -> None:
        code = "005930"
        self.open_unassigned(code, "2026-08-23T09:00:00+09:00")
        episode = self.transition(
            code,
            self.target("A", "G", "Alpha", "Group"),
            "2026-08-23T09:10:00+09:00",
        )
        self.append(
            code,
            episode.episode_id,
            net=0,
            gross=0,
            cost=100,
            trade_date="2026-08-23",
        )
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="Samsung", assigned_routine_instance_id="A")],
            instances=[SimpleNamespace(instance_id="A", group_id="G", display_name="Alpha")],
            groups=[SimpleNamespace(group_id="G", display_name="Group")],
        )
        harness = SimpleNamespace(_canonical_performance_snapshot_error="")
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )

        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            snapshot.stock_rows("current")[0],
            snapshot,
        )

        self.assertEqual("1", payload["performance_period_value"])
        self.assertEqual("0", payload["performance_profit_amount"])
        self.assertEqual("0.00%", payload["performance_profit_rate"])
        self.assertEqual("0", payload["performance_average_amount"])
        self.assertEqual("0.00%", payload["performance_average_rate"])
        self.assertIn("VALID_ZERO", payload["performance_profit_tooltip"])

    def test_parent_diagnostic_payload_distinguishes_unresolved_ownership(self) -> None:
        code = "003550"
        self.open_unassigned(code, "2026-08-23T09:00:00+09:00")
        episode = self.transition(
            code,
            self.target("A", "G", "Alpha", "Group"),
            "2026-08-23T09:10:00+09:00",
        )
        self.append(
            code,
            episode.episode_id,
            net=0,
            gross=0,
            cost=100,
            trade_date="2026-08-23",
            owner_id=OWNERSHIP_UNRESOLVED,
        )
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="LG", assigned_routine_instance_id="A")],
            instances=[SimpleNamespace(instance_id="A", group_id="G", display_name="Alpha")],
            groups=[SimpleNamespace(group_id="G", display_name="Group")],
        )
        harness = SimpleNamespace(_canonical_performance_snapshot_error="")
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )

        parent = snapshot.instance_rows("current")[0]
        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            parent,
            snapshot,
        )

        self.assertIn(
            "NO_RESOLVED_PERFORMANCE_OWNERSHIP",
            payload["performance_profit_tooltip"],
        )

    def test_parent_diagnostic_payload_distinguishes_performance_outside_current_scope(self) -> None:
        code = "005930"
        self.open_unassigned(code, "2026-08-20T09:00:00+09:00")
        historical = self.transition(
            code,
            self.target("A", "G", "Alpha", "Group"),
            "2026-08-20T09:10:00+09:00",
        )
        self.append(
            code,
            historical.episode_id,
            net=10,
            gross=10,
            cost=100,
            trade_date="2026-08-20",
        )
        self.transition(code, AssignmentEpisodeTarget.unassigned(), "2026-08-21T09:00:00+09:00")
        self.transition(
            code,
            self.target("A", "G", "Alpha", "Group"),
            "2026-08-22T09:00:00+09:00",
        )
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="Samsung", assigned_routine_instance_id="A")],
            instances=[SimpleNamespace(instance_id="A", group_id="G", display_name="Alpha")],
            groups=[SimpleNamespace(group_id="G", display_name="Group")],
        )
        harness = SimpleNamespace(_canonical_performance_snapshot_error="")
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )

        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            snapshot.instance_rows("current")[0],
            snapshot,
        )

        self.assertIn(
            "NO_PERFORMANCE_IN_SCOPE",
            payload["performance_profit_tooltip"],
        )

    def test_incomplete_net_is_not_displayed_as_zero(self) -> None:
        code = "005930"
        self.open_unassigned(code, "2026-08-23T09:00:00+09:00")
        episode = self.transition(
            code,
            self.target("A", "G", "Alpha", "Group"),
            "2026-08-23T09:10:00+09:00",
        )
        self.append(code, episode.episode_id, net=None, gross=10, cost=100, trade_date="2026-08-23")
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="Samsung", assigned_routine_instance_id="A")],
            instances=[SimpleNamespace(instance_id="A", group_id="G", display_name="Alpha")],
            groups=[SimpleNamespace(group_id="G", display_name="Group")],
        )
        row = snapshot.stock_rows("all")[0]
        metrics = snapshot.metric_result(row)
        self.assertEqual(MetricStatus.INCOMPLETE, metrics.profit_amount.status)
        harness = SimpleNamespace(
            _canonical_performance_snapshot_error="",
        )
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )
        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness, row, snapshot
        )
        self.assertEqual("-", payload["performance_profit_amount"])
        self.assertIn("NET_PNL_INCOMPLETE", payload["performance_profit_tooltip"])

    def test_current_relation_mismatch_remains_in_internal_diagnostic_payload(self) -> None:
        code = "005930"
        self.open_unassigned(code, "2026-08-23T09:00:00+09:00")
        episode = self.transition(
            code,
            self.target("A", "G1", "Alpha", "Group One"),
            "2026-08-23T09:10:00+09:00",
        )
        self.append(code, episode.episode_id, net=10, gross=10, cost=100, trade_date="2026-08-23")
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=[SimpleNamespace(code=code, name="Samsung", assigned_routine_instance_id="B")],
            instances=[SimpleNamespace(instance_id="B", group_id="G2", display_name="Beta")],
            groups=[SimpleNamespace(group_id="G2", display_name="Group Two")],
        )
        stock = snapshot.stock_rows("current")[0]
        instance = snapshot.instance_rows("current")[0]
        group = snapshot.group_rows("current")[0]

        self.assertIn("INSTANCE_ID_MISMATCH", snapshot.identity_tooltip(stock))
        self.assertIn("GROUP_ID_MISMATCH", snapshot.identity_tooltip(instance))
        self.assertIn("GROUP_ID_MISMATCH", snapshot.identity_tooltip(group))

        harness = SimpleNamespace(_canonical_performance_snapshot_error="")
        harness._canonical_metric_status_rank = MethodType(
            AutoTradeSettingWindow._canonical_metric_status_rank,
            harness,
        )
        harness._canonical_metric_tooltip = MethodType(
            AutoTradeSettingWindow._canonical_metric_tooltip,
            harness,
        )
        payload = AutoTradeSettingWindow._routine_tree_canonical_performance_texts(
            harness,
            instance,
            snapshot,
        )
        self.assertEqual("0", payload["performance_profit_amount"])
        self.assertIn(
            "NO_RESOLVED_PERFORMANCE_OWNERSHIP",
            payload["performance_profit_tooltip"],
        )
        self.assertIn("INSTANCE_ID_MISMATCH", payload["performance_profit_tooltip"])
        self.assertIn("GROUP_ID_MISMATCH", payload["performance_profit_tooltip"])

    def test_past_tree_keeps_moved_instance_under_episode_group(self) -> None:
        from tests.test_auto_trade_setting_routine_tree import (
            AutoTradeSettingRoutineTreeTest,
        )

        code = "005930"
        self.open_unassigned(code, "2026-08-20T09:00:00+09:00")
        past = self.transition(
            code,
            self.target("A", "G1", "Alpha Old", "Group One"),
            "2026-08-20T09:10:00+09:00",
        )
        self.transition(
            code,
            self.target("A", "G2", "Alpha New", "Group Two"),
            "2026-08-21T09:10:00+09:00",
        )
        self.append(code, past.episode_id, net=100, gross=100, cost=1000, trade_date="2026-08-20")

        definition = SimpleNamespace(
            definition_id="indicator_follow",
            display_name="Indicator Follow",
            package_dir=str(self.root / "routines" / "indicator_follow"),
        )
        instance = SimpleNamespace(
            instance_id="A",
            definition_id="indicator_follow",
            group_id="G2",
            display_name="Alpha Current",
            rules_path="",
        )
        groups = [
            SimpleNamespace(
                group_id="G1",
                definition_id="indicator_follow",
                display_name="Group One",
                name="Group One",
                path=self.root / "groups" / "G1",
            ),
            SimpleNamespace(
                group_id="G2",
                definition_id="indicator_follow",
                display_name="Group Two",
                name="Group Two",
                path=self.root / "groups" / "G2",
            ),
        ]
        stocks = [
            {
                "code": code,
                "name": "Samsung",
                "stock_path": "stocks/005930_Samsung",
                "assigned_routine_instance_id": "A",
                "routines": [],
            }
        ]
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=stocks,
            instances=[instance],
            groups=groups,
        )
        window = AutoTradeSettingRoutineTreeTest()._window_harness()
        window._routine_tree_projected_instance_ids_override = None
        window._routine_tree_display_level = "routine"
        window._routine_tree_display_scope = "historical"
        window._routine_instance_operation_counts = lambda: {
            "A": {"registered": 1, "normal": 1}
        }
        window._auto_trade_initial_read_snapshot = {
            "definitions": (definition,),
            "instances": (instance,),
            "groups": tuple(groups),
            "stocks": tuple(stocks),
            "canonical_performance": snapshot,
            "canonical_performance_error": "",
        }
        window.load_routine_table()

        instance_rows = [
            window.routine_table.item(row, 0).data(Qt.UserRole)
            for row in range(window.routine_table.rowCount())
            if not window.routine_table.isRowHidden(row)
            and window.routine_table.item(row, 0).data(
                Qt.UserRole
            ).get("row_kind")
            == "instance"
        ]
        self.assertEqual(1, len(instance_rows))
        self.assertEqual("G1", instance_rows[0]["group_id"])
        self.assertEqual("A", instance_rows[0]["instance_id"])
        self.assertEqual("Alpha Old", instance_rows[0]["display_name"])
        self.assertEqual("+100", instance_rows[0]["performance_profit_amount"])
        stock_rows = [
            window.routine_table.item(row, 0).data(Qt.UserRole)
            for row in range(window.routine_table.rowCount())
            if not window.routine_table.isRowHidden(row)
            and window.routine_table.item(row, 0).data(Qt.UserRole).get("row_kind") == "stock"
        ]
        self.assertEqual(1, len(stock_rows))
        self.assertEqual("CURRENT", stock_rows[0]["stock_relation_kind"])
        self.assertFalse(stock_rows[0]["is_historical"])
        self.assertEqual("\u2713", stock_rows[0]["tree_icon"])

    def test_tree_allows_historical_instances_but_flat_view_prefers_current(self) -> None:
        from tests.test_auto_trade_setting_routine_tree import (
            AutoTradeSettingRoutineTreeTest,
        )

        code = "005930"
        self.open_unassigned(code, "2026-08-20T09:00:00+09:00")
        episode_a = self.transition(
            code,
            self.target("A", "G1", "Alpha", "Group One"),
            "2026-08-20T09:10:00+09:00",
        )
        self.append(code, episode_a.episode_id, net=10, gross=10, cost=100, trade_date="2026-08-20")
        episode_b = self.transition(
            code,
            self.target("B", "G1", "Beta", "Group One"),
            "2026-08-21T09:10:00+09:00",
        )
        self.append(code, episode_b.episode_id, net=20, gross=20, cost=200, trade_date="2026-08-21")
        episode_c = self.transition(
            code,
            self.target("C", "G1", "Charlie", "Group One"),
            "2026-08-22T09:10:00+09:00",
        )
        self.append(code, episode_c.episode_id, net=30, gross=30, cost=300, trade_date="2026-08-22")

        definition = SimpleNamespace(
            definition_id="indicator_follow",
            display_name="Indicator Follow",
            package_dir=str(self.root / "routines" / "indicator_follow"),
        )
        instances = [
            SimpleNamespace(
                instance_id=instance_id,
                definition_id="indicator_follow",
                group_id="G1",
                display_name=display_name,
                rules_path="",
            )
            for instance_id, display_name in (
                ("A", "Alpha"),
                ("B", "Beta"),
                ("C", "Charlie"),
            )
        ]
        groups = [
            SimpleNamespace(
                group_id="G1",
                definition_id="indicator_follow",
                display_name="Group One",
                name="Group One",
                path=self.root / "groups" / "G1",
            )
        ]
        stocks = [
            {
                "code": code,
                "name": "Samsung",
                "stock_path": "stocks/005930_Samsung",
                "assigned_routine_instance_id": "C",
                "routines": [],
            }
        ]
        snapshot = build_canonical_performance_ui_snapshot(
            self.root,
            stocks=stocks,
            instances=instances,
            groups=groups,
        )
        window = AutoTradeSettingRoutineTreeTest()._window_harness()
        window._routine_tree_projected_instance_ids_override = None
        window._routine_tree_display_level = "routine"
        window._routine_tree_display_scope = "all"
        window._routine_tree_valid_only = False
        window._routine_instance_operation_counts = lambda: {
            instance.instance_id: {"registered": 1, "normal": 1}
            for instance in instances
        }
        window._auto_trade_initial_read_snapshot = {
            "definitions": (definition,),
            "instances": tuple(instances),
            "groups": tuple(groups),
            "stocks": tuple(stocks),
            "canonical_performance": snapshot,
            "canonical_performance_error": "",
        }
        window.load_routine_table()

        hierarchical_rows = [
            window.routine_table.item(row, 0).data(Qt.UserRole)
            for row in range(window.routine_table.rowCount())
            if not window.routine_table.isRowHidden(row)
            and window.routine_table.item(row, 0).data(Qt.UserRole).get("row_kind") == "stock"
        ]
        self.assertEqual(
            {("A", "HISTORICAL", "\u25aa"), ("B", "HISTORICAL", "\u25aa"), ("C", "CURRENT", "\u2713")},
            {
                (
                    row["instance_id"],
                    row["stock_relation_kind"],
                    row["tree_icon"],
                )
                for row in hierarchical_rows
            },
        )

        window._routine_tree_display_level = "stock"
        window._routine_tree_valid_only = True
        window.load_routine_table()
        flat_rows = [
            window.routine_table.item(row, 0).data(Qt.UserRole)
            for row in range(window.routine_table.rowCount())
            if not window.routine_table.isRowHidden(row)
            and window.routine_table.item(row, 0).data(Qt.UserRole).get("row_kind") == "stock"
        ]
        self.assertEqual(1, len(flat_rows))
        self.assertEqual("C", flat_rows[0]["instance_id"])
        self.assertEqual("CURRENT", flat_rows[0]["stock_relation_kind"])
        self.assertEqual(("A", "B"), flat_rows[0]["historical_instance_ids"])
        self.assertEqual("\u2713", flat_rows[0]["tree_icon"])

    def test_non_zero_payload_reaches_existing_fixed_qt_labels(self) -> None:
        from tests.test_auto_trade_setting_routine_tree import (
            AutoTradeSettingRoutineTreeTest,
        )

        snapshot = self.fixture()
        stock = next(row for row in snapshot.stock_rows("all") if row.stock_code == "005930")
        harness = AutoTradeSettingRoutineTreeTest()._window_harness()
        payload = harness._routine_tree_canonical_performance_texts(stock, snapshot)
        row_data = {
            "row_kind": "stock",
            "display_name": "삼성전자",
            "tree_icon": "\u2713",
            "stock_code": "005930",
            "instance_id": "A",
            **payload,
        }
        harness._configure_routine_tree_row_layout([row_data])
        widget = harness._routine_tree_row_widget(row_data, "삼성전자")

        profit = widget.findChild(
            QLabel,
            "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
        )
        profit_rate = widget.findChild(
            QLabel,
            "autoTradeSettingRoutineTreePerformanceProfitRightValue",
        )
        average = widget.findChild(
            QLabel,
            "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
        )
        self.assertEqual("+180", profit.text())
        self.assertEqual("+10.00%", profit_rate.text())
        self.assertEqual("+60", average.text())
        self.assertIn("VALID", payload["performance_profit_tooltip"])
        self.assertEqual("", widget.toolTip())
        for metric_name in ("Period", "Profit", "Average", "Efficiency"):
            metric = widget.findChild(
                QWidget,
                f"autoTradeSettingRoutineTreePerformance{metric_name}",
            )
            self.assertIsNotNone(metric)
            self.assertEqual("", metric.toolTip())
            self.assertTrue(
                all(label.toolTip() == "" for label in metric.findChildren(QLabel))
            )

    def test_tree_read_path_no_longer_calls_legacy_performance_calculators(self) -> None:
        source = inspect.getsource(AutoTradeSettingWindow.load_routine_table)
        self.assertNotIn("_routine_tree_stock_performance_source", source)
        self.assertNotIn("_routine_tree_performance_texts", source)
        self.assertNotIn("_historical_stocks_by_instance", source)
        self.assertIn("_routine_tree_canonical_performance_texts", source)

    def test_canonical_sort_orders_status_then_value_then_name_and_id(self) -> None:
        harness = SimpleNamespace(
            _routine_tree_row_sort_value=lambda row, criterion: float(
                row[f"performance_{criterion}_sort_value"]
            )
        )
        rows = [
            {
                "display_name": "Beta",
                "instance_id": "2",
                "performance_profit_sort_value": 1000,
                "performance_profit_sort_status_rank": 1,
            },
            {
                "display_name": "Alpha",
                "instance_id": "1",
                "performance_profit_sort_value": 10,
                "performance_profit_sort_status_rank": 0,
            },
            {
                "display_name": "Beta",
                "instance_id": "3",
                "performance_profit_sort_value": 20,
                "performance_profit_sort_status_rank": 0,
            },
        ]
        ordered = sorted(
            rows,
            key=lambda row: AutoTradeSettingWindow._routine_tree_canonical_sort_key(
                harness,
                row,
                "profit",
            ),
        )
        self.assertEqual(["3", "1", "2"], [row["instance_id"] for row in ordered])


if __name__ == "__main__":
    unittest.main()
