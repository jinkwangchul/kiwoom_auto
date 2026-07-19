# gui_indicator_follow_routine_settings_dialog.py
# STEP38 routine settings dialog
#
# 목적:
# - 첫 진입 화면을 rules.json 확인창이 아니라 루틴 컨트롤 패널로 구성한다.
# - 각 설정 영역의 활성/비활성 상태를 직관적으로 보여준다.
# - 상세 설정은 별도 탭/버튼으로 진입한다.
#
# 현재 범위:
# - rules.json 읽기
# - 컨트롤 패널 표시
# - 매수/매도/고급/검증 탭 표시
# - 저장 기능 비활성
#
# 금지:
# - 신규 신호 개념 추가 금지
# - 주문취소 구현 금지
# - BUY 확장 구현 금지
# - 실주문 연결 금지
# - rules.json 저장 금지

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class _TabHostStub:
    """Mixin addTab 호환용. QTabWidget 없이 구성 탭을 메인 레이아웃에 직접 붙인다."""

    def addTab(self, widget, label):
        return 0

    def setCurrentWidget(self, widget):
        pass


from gui_indicator_follow_common_widgets import IndicatorFollowCommonWidgetsMixin
from gui_indicator_follow_control_tab import IndicatorFollowControlTabMixin
from gui_indicator_follow_buy_controls import IndicatorFollowBuyControlsMixin
from gui_indicator_follow_sell_controls import IndicatorFollowSellControlsMixin
from gui_routine_registry import get_routine_records
import rule_approval_session_file_service as rule_approval_session_file_service
from routine_instance_registry import load_persisted_routine_instances, load_routine_definitions
from routine_instance_repository import RoutineInstanceRepository


DEFAULT_BUY_SIGNAL_EXPR = "A and B and C and D"


def _is_restore_test_expression(value):
    text = str(value or "").strip().upper()
    return text.startswith("RESTORE_TEST_") and text.endswith("_EXPR")


def normalize_indicator_follow_basic_ui_state(state):
    """Return a copied UI state with invalid test BUY expressions restored."""
    if not isinstance(state, dict):
        return state
    normalized = deepcopy(state)
    basic = normalized.get("basic")
    if not isinstance(basic, dict):
        return normalized
    buy_expr = str(basic.get("buy_signal_expr_line") or "").strip()
    if not buy_expr or _is_restore_test_expression(buy_expr):
        basic["buy_signal_expr_line"] = DEFAULT_BUY_SIGNAL_EXPR
    return normalized


class IndicatorFollowRoutineSettingsDialog(
    IndicatorFollowControlTabMixin,
    IndicatorFollowBuyControlsMixin,
    IndicatorFollowSellControlsMixin,
    IndicatorFollowCommonWidgetsMixin,
    QDialog,
):
    """
    Routine settings dialog.

    STEP38:
    - 첫 화면 = 컨트롤 패널
    - 설명문/JSON/경로 노출 최소화
    - 항목별 활성/비활성 상태와 진입 버튼 중심
    """

    def __init__(
        self,
        rules_path=None,
        routine_path=None,
        routine_name=None,
        parent=None,
        *,
        definition_id=None,
        definition_display_name=None,
        instance_id=None,
        settings_mode=None,
    ):
        super().__init__(parent)
        self.routine_path = Path(routine_path) if routine_path else None
        self.routine_name = str(routine_name or "").strip()
        self.definition_id = str(definition_id or "").strip()
        self.definition_display_name = str(definition_display_name or routine_name or "").strip()
        self.instance_id = str(instance_id or "").strip()
        inferred_mode = "edit" if self.instance_id else "registration"
        self.settings_mode = str(settings_mode or inferred_mode).strip().lower()
        if self.settings_mode not in {"registration", "edit"}:
            raise ValueError("settings_mode must be registration or edit")
        if self.settings_mode == "edit" and not self.instance_id:
            raise ValueError("edit mode requires instance_id")
        if self.settings_mode == "registration" and self.instance_id:
            raise ValueError("registration mode cannot use instance_id")
        self._update_window_title()
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        # \uad6c\uc131 \uc694\uc57d \uc0c1\ud0dc\uc5d0\uc11c\ub294 \ud5e4\ub354 3\uc904\ub9cc \ubcf4\uc774\ub3c4\ub85d \ucc3d \ub192\uc774\ub97c \uc904\uc77c \uc218 \uc788\uc5b4\uc57c \ud55c\ub2e4.
        # \ub2e4\ub978 \uae30\uc874 \uc791\uc5c5 \uae30\uc900\uc740 \uc720\uc9c0\ud558\uace0, \ub192\uc774 \ucd5c\uc18c\uac12\ub9cc \ub0ae\ucd98\ub2e4.
        self.resize(2360, 420)
        self.setMinimumSize(1600, 360)

        self.rules_path = Path(rules_path) if rules_path else self._default_rules_path()
        if not self.definition_id:
            self.definition_id = self._default_definition_id()
        self.rules_data = {}
        self._approval_session_path = self._default_rule_approval_session_path()

        self._build_ui()
        self.load_rules()
        QTimer.singleShot(0, self.showMaximized)

    def _default_rules_path(self):
        for record in get_routine_records():
            settings_ui = str(getattr(record, "settings_ui", "") or "").strip().lower()
            if settings_ui == "indicator_follow":
                self.routine_path = record.path
                if not self.routine_name:
                    self.routine_name = record.name
                return record.rules_path
        here = Path(__file__).resolve().parent
        return here / "routines" / "rules.json"

    def _update_window_title(self):
        if self.instance_id:
            self.setWindowTitle(f"{self.routine_name or 'Routine'} 설정")
        else:
            self.setWindowTitle(f"{self.routine_name or 'Routine'} 신규 등록설정")

    def _default_rule_approval_session_path(self):
        return (
            Path(__file__).resolve().parent
            / "runtime"
            / "routines"
            / "indicator_follow"
            / "approval_session.json"
        )

    def _default_definition_id(self):
        routine_path = self.routine_path.resolve() if self.routine_path is not None else None
        for definition in load_routine_definitions():
            if routine_path is not None and definition.package_dir.resolve() == routine_path:
                return definition.definition_id
            if definition.display_name == self.routine_name:
                return definition.definition_id
        return ""

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        self.title_label = QLabel(self.routine_name or "")
        self.title_label.setVisible(False)

        self.tabs = _TabHostStub()

        self._build_control_tab()
        root.addWidget(self.control_tab, 1)

        # The official settings UI is the single control_tab view. Keep the
        # legacy tab builders defined, but do not instantiate their hidden
        # widgets here because they overwrite self.buy_*/self.sell_* refs.
        # self._build_buy_tab()
        # self._build_sell_tab()
        # self._build_buy_edit_tab()
        # self._build_sell_edit_tab()
        # Build validation widgets for preview/diagnostic code paths.
        self._build_validation_tab()

        button_row = QHBoxLayout()
        self.reload_button = QPushButton("다시 불러오기")
        self.validate_button = QPushButton("설정 검증")
        if self.settings_mode == "edit":
            self.register_button = QPushButton("다른 이름으로 등록")
            self.register_button.setObjectName("routineRegisterButton")
            self.save_button = QPushButton("저장")
        else:
            self.save_button = QPushButton("등록")
            self.save_button.setObjectName("routineRegisterButton")
            self.register_button = self.save_button
        self.close_button = QPushButton("닫기")

        self.save_button.setEnabled(True)

        self.reload_button.clicked.connect(self.load_rules)
        self.validate_button.clicked.connect(
            lambda: QMessageBox.information(
                self,
                "설정 검증",
                "\n".join(
                    [
                        f"신호 구조: {self.validation_signal_line.text()}",
                        f"실주문 실행: {self.validation_execution_line.text()}",
                        f"매도 구조: {self.validation_sell_line.text()}",
                        f"매수 확장: {self.validation_buy_line.text()}",
                    ]
                ),
            )
        )
        self.validate_button.clicked.connect(self._handle_validate_clicked)
        if self.settings_mode == "edit":
            self.save_button.clicked.connect(self.save_indicator_follow_ui_state_to_rules)
            self.register_button.clicked.connect(self.open_registration_dialog)
        else:
            self.save_button.clicked.connect(self.open_registration_dialog)
        self.close_button.clicked.connect(self.close)

        button_row.addWidget(self.reload_button)
        button_row.addWidget(self.validate_button)
        button_row.addStretch(1)
        if self.settings_mode == "edit":
            button_row.addWidget(self.register_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.close_button)
        root.addLayout(button_row)
























































        # STEP41B: 매수 탭은 공식 UI에서 제거. 기존 로딩 호환 위젯만 유지.

        # STEP41B: 매도 탭은 공식 UI에서 제거. 기존 로딩 호환 위젯만 유지.




        # 매수 탭은 구성탭 통합 방식으로 전환되어 노출하지 않음

        # 매도 탭은 구성탭 통합 방식으로 전환되어 노출하지 않음

    def _build_advanced_tab(self):
        self.advanced_tab = QWidget()
        layout = QVBoxLayout(self.advanced_tab)

        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "고급/확장 설정\n\n"
            "현재 잠금:\n"
            "- 다중매수\n"
            "- 다중호가\n"
            "- 다중지점\n"
            "- 지속매수\n"
            "- 평단 중심 매수강도\n"
            "- 능동매수\n"
            "- 루틴 주문취소\n\n"
            "위 항목은 개념 확정 후 별도 설정 화면으로 연결합니다."
        )
        layout.addWidget(text, 1)

    def _build_validation_tab(self):
        self.validation_tab = QWidget()
        layout = QVBoxLayout(self.validation_tab)

        box = QGroupBox("검증 결과")
        form = QFormLayout(box)

        self.validation_signal_line = self._readonly_line()
        self.validation_execution_line = self._readonly_line()
        self.validation_sell_line = self._readonly_line()
        self.validation_buy_line = self._readonly_line()

        form.addRow("신호 구조", self.validation_signal_line)
        form.addRow("실주문 실행", self.validation_execution_line)
        form.addRow("매도 구조", self.validation_sell_line)
        form.addRow("매수 확장", self.validation_buy_line)

        layout.addWidget(box)

        self._rule_approval_session = {}
        self._rule_approval_decision_widgets = {}
        self._last_rule_engine_preview = {}
        self._last_rule_pipeline_preview = {}
        self._last_rule_validation_context = {}
        self._rule_approval_session_dirty = False
        self._last_saved_rule_approval_session_decisions = None
        self._last_rule_approval_session_save_result = {}
        self._rule_approval_save_button = None
        self._rule_approval_controls_box = QGroupBox("Rule Candidate Approval Preview Controls")
        self._rule_approval_controls_layout = QGridLayout(self._rule_approval_controls_box)
        self._rule_approval_controls_box.setVisible(False)
        layout.addWidget(self._rule_approval_controls_box)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setVisible(False)
        layout.addWidget(self.preview_text)

        self.developer_button = QPushButton("개발자 정보 보기/숨기기")
        self.developer_button.clicked.connect(
            lambda: self.preview_text.setVisible(not self.preview_text.isVisible())
        )
        layout.addWidget(self.developer_button)

        layout.addStretch(1)

    def load_rules(self):
        if not self.rules_path.exists():
            self.rules_data = {}
            self._update_window_title()
            QMessageBox.warning(self, "rules.json 없음", f"rules.json을 찾을 수 없습니다.\n{self.rules_path}")
            self._clear_fields()
            return

        try:
            with self.rules_path.open("r", encoding="utf-8") as f:
                self.rules_data = json.load(f)
        except Exception as exc:
            self.rules_data = {}
            self._update_window_title()
            QMessageBox.critical(self, "로드 실패", f"rules.json 로드 실패\n{exc}")
            self._clear_fields()
            return

        self._populate_fields()
        self.refresh_preview()
        self._update_window_title()

    def _clear_fields(self):
        self._set_card_status(self.card_routine, "로드 실패", "error")
        self._set_card_status(self.card_buy, "확인 불가", "error")
        self._set_card_status(self.card_sell, "확인 불가", "error")
        self._set_card_status(self.card_profit, "확인 불가", "error")
        self._set_card_status(self.card_advanced, "잠금", "locked")
        self._set_card_status(self.card_validation, "오류", "error")

        self.preview_text.clear()

    def _get_indicator_follow_ui_state_from_rules(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        if not isinstance(rules, dict):
            return None

        ui_state_root = rules.get("indicator_follow_ui_state")
        if not isinstance(ui_state_root, dict):
            return None

        ui_state = ui_state_root.get("state")
        return ui_state if isinstance(ui_state, dict) else None

    def _apply_indicator_follow_ui_state_from_rules(self):
        self._last_ui_state_apply_result = None
        ui_state = self._get_indicator_follow_ui_state_from_rules()
        if not isinstance(ui_state, dict):
            return None

        try:
            result = self.apply_indicator_follow_ui_state(ui_state)
        except Exception as exc:
            result = {
                "applied": [],
                "skipped": [],
                "sync_errors": [{
                    "name": "indicator_follow_ui_state",
                    "error": str(exc),
                }],
            }
        self._last_ui_state_apply_result = result
        return result

    def _populate_fields(self):
        data = self.rules_data

        routine_name = data.get("routine_name") or data.get("name") or self.routine_name or (self.routine_path.name if self.routine_path else "Routine")
        self.title_label.setText(str(routine_name))

        principle = data.get("principle", {}) if isinstance(data.get("principle", {}), dict) else {}

        enabled = bool(data.get("enabled", True))
        signal_only = bool(data.get("signal_only", principle.get("signal_only", True)))
        execution_enabled = bool(data.get("execution_enabled", principle.get("execution_enabled", False)))

        buy = data.get("buy", {}) if isinstance(data.get("buy", {}), dict) else {}
        buy_enabled = bool(buy.get("enabled", True))
        buy_delay = buy.get("delay_bar", "")

        sell = data.get("sell", {}) if isinstance(data.get("sell", {}), dict) else {}
        sell_enabled = bool(sell.get("enabled", True))
        sell_logic = str(sell.get("signal_logic", "OR")).upper()
        if sell_logic not in ("OR", "AND"):
            sell_logic = "OR"

        signals = sell.get("signals", {}) if isinstance(sell.get("signals", {}), dict) else {}

        sell_reversal_signal = signals.get("macd_sell", {}) if isinstance(signals.get("macd_sell", {}), dict) else {}
        sell_reversal_enabled = bool(sell_reversal_signal.get("enabled", True))
        sell_reversal_delay = sell_reversal_signal.get("delay_bar", sell.get("delay_bar", ""))

        profit_sell = signals.get("profit_rate_sell", {}) if isinstance(signals.get("profit_rate_sell", {}), dict) else {}
        profit_sell_enabled = bool(profit_sell.get("enabled", False))
        target = (
            profit_sell.get("target_profit_rate")
            if profit_sell.get("target_profit_rate") is not None
            else profit_sell.get("profit_rate_percent", None)
        )
        basis = profit_sell.get("basis", "average_price")

        # 컨트롤 패널 상태
        self._set_card_status(self.card_routine, "활성" if enabled else "비활성", "active" if enabled else "inactive")
        self._set_card_status(self.card_buy, "활성" if buy_enabled else "비활성", "active" if buy_enabled else "inactive")
        self._set_card_status(self.card_sell, "활성" if sell_enabled else "비활성", "active" if sell_enabled else "inactive")
        self._set_card_status(self.card_profit, "활성" if profit_sell_enabled else "비활성", "active" if profit_sell_enabled else "inactive")
        self._set_card_status(self.card_advanced, "잠금", "locked")
        self._set_card_status(self.card_validation, "정상", "active")

        # 매수 탭
        if hasattr(self, "buy_enabled_check"):
            self.buy_enabled_check.setChecked(buy_enabled)
        if hasattr(self, "buy_delay_line"):
            self.buy_delay_line.setText(str(buy_delay))
        if hasattr(self, "buy_status_line"):
            self.buy_status_line.setText("기본 매수 구조 사용" if buy_enabled else "매수 비활성")

        # 매도 탭
        if hasattr(self, "sell_enabled_check"):
            self.sell_enabled_check.setChecked(sell_enabled)
        if hasattr(self, "sell_logic_combo"):
            self.sell_logic_combo.setCurrentText(sell_logic)

        if hasattr(self, "macd_sell_enabled_check"):
            self.macd_sell_enabled_check.setChecked(sell_reversal_enabled)
        if hasattr(self, "macd_sell_delay_line"):
            self.macd_sell_delay_line.setText(str(sell_reversal_delay))
        if hasattr(self, "macd_sell_status_line"):
            self.macd_sell_status_line.setText("사용" if sell_reversal_enabled else "비활성")

        if hasattr(self, "profit_sell_enabled_check"):
            self.profit_sell_enabled_check.setChecked(profit_sell_enabled)
        if target is None:
            if hasattr(self, "target_profit_line"):
                self.target_profit_line.setText("미설정")
        else:
            if hasattr(self, "target_profit_line"):
                self.target_profit_line.setText(f"{target} %")
        if hasattr(self, "profit_basis_line"):
            self.profit_basis_line.setText("평단 대비 현재가" if basis == "average_price" else str(basis))

        # 검증 탭
        self.validation_signal_line.setText("BUY / SELL / signal=None")
        self.validation_execution_line.setText("비활성" if not execution_enabled else "활성")
        self.validation_sell_line.setText(f"{sell_logic} 결합")
        self.validation_buy_line.setText("확장 잠금")

        if not signal_only:
            self._set_card_status(self.card_validation, "확인 필요", "locked")
            self.validation_signal_line.setText("signal_only 비활성 확인 필요")

        if execution_enabled:
            self._set_card_status(self.card_validation, "주의", "locked")
            self.validation_execution_line.setText("활성 - 실주문 전 확인 필요")

        self._apply_indicator_follow_ui_state_from_rules()

    def refresh_preview(self):
        if not self.rules_data:
            self.preview_text.setPlainText("개발자 정보 없음")
            return

        data = self.rules_data
        buy = data.get("buy", {}) if isinstance(data.get("buy", {}), dict) else {}
        sell = data.get("sell", {}) if isinstance(data.get("sell", {}), dict) else {}
        signals = sell.get("signals", {}) if isinstance(sell.get("signals", {}), dict) else {}
        buy_delay_bar = (
            self.buy_delay_line.text()
            if hasattr(self, "buy_delay_line")
            else str(buy.get("delay_bar", ""))
        )
        sell_signal_logic = (
            self.sell_logic_combo.currentText()
            if hasattr(self, "sell_logic_combo")
            else str(sell.get("signal_logic", "OR")).upper()
        )

        preview = {
            "rules_path": str(self.rules_path),
            "routine_name": self.title_label.text(),
            "rules_version": data.get("rules_version") or data.get("version") or data.get("schema_version") or "",
            "enabled": self.card_routine["status"].text(),
            "buy": {
                "status": self.card_buy["status"].text(),
                "delay_bar": buy_delay_bar,
            },
            "sell": {
                "status": self.card_sell["status"].text(),
                "signal_logic": sell_signal_logic,
                "macd_sell": self.card_sell["status"].text(),
                "profit_rate_sell": self.card_profit["status"].text(),
            },
            "advanced": self.card_advanced["status"].text(),
            "validation": self.card_validation["status"].text(),
            "raw_sell_keys": list(signals.keys()),
        }

        self.preview_text.setPlainText(json.dumps(preview, ensure_ascii=False, indent=2))

    def build_rules_with_indicator_follow_ui_state(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        rules_copy = deepcopy(rules) if isinstance(rules, dict) else {}
        state = self.collect_indicator_follow_ui_state()
        rules_copy["indicator_follow_ui_state"] = {
            "ui_state_version": "0.1",
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "state": state,
        }
        return rules_copy

    def build_registration_rules_from_current_ui_state(self):
        """Build a validated, non-writing rules snapshot for a new instance."""
        try:
            rules_with_ui_state = self.build_rules_with_indicator_follow_ui_state()
            ui_state = self.collect_indicator_follow_ui_state()
            mapper = self._load_indicator_follow_rule_mapper()
            pending_result = mapper.build_engine_rules_pending_from_ui_state(
                ui_state,
                rules_with_ui_state,
            )
            pending_rules = pending_result.get("pending_rules", {})
            pending = pending_rules.get("indicator_follow_rule_pending", {})
            if not isinstance(pending_rules, dict) or not isinstance(pending, dict):
                raise ValueError("공식 rules 변환 결과가 올바르지 않습니다.")
            if pending.get("mode") == "error":
                raise ValueError("공식 rules 변환 검증이 실패했습니다.")
            return {
                "success": True,
                "rules": pending_rules,
                "validation_warnings": list(pending_result.get("validation_warnings", [])),
                "postponed": list(pending_result.get("postponed", [])),
                "error": "",
            }
        except Exception as exc:
            return {
                "success": False,
                "rules": {},
                "validation_warnings": [],
                "postponed": [],
                "error": str(exc),
            }

    def open_registration_dialog(self):
        from gui_routine_registration_dialog import (
            RoutineRegistrationDialog,
            suggest_routine_instance_display_name,
        )

        if not self.definition_id:
            QMessageBox.warning(self, "루틴 등록", "현재 루틴 유형의 definition_id를 확인할 수 없습니다.")
            return None

        existing_names = [
            item.display_name
            for item in load_persisted_routine_instances()
            if item.definition_id == self.definition_id
        ]
        suggested_name = suggest_routine_instance_display_name(
            self.definition_display_name,
            len(existing_names),
        )
        dialog = RoutineRegistrationDialog(
            definition_id=self.definition_id,
            definition_display_name=self.definition_display_name,
            initial_display_name=suggested_name,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted or dialog.registration_request is None:
            return None

        rules_result = self.build_registration_rules_from_current_ui_state()
        if rules_result.get("success") is not True:
            QMessageBox.critical(
                self,
                "루틴 등록 실패",
                "현재 설정을 공식 rules 경로로 변환하지 못했습니다.\n"
                f"{rules_result.get('error', '')}",
            )
            return None

        repository = RoutineInstanceRepository(Path(__file__).resolve().parent)
        result = repository.create_instance(
            dialog.registration_request,
            rules_result.get("rules", {}),
        )
        if not result.success or result.instance is None:
            QMessageBox.critical(
                self,
                "루틴 등록 실패",
                result.error or "등록 루틴을 저장하지 못했습니다.",
            )
            return None

        self.last_registered_instance_id = result.instance.instance_id
        parent = self.parent()
        refresh_all = getattr(parent, "refresh_all", None)
        if callable(refresh_all):
            refresh_all()
        QMessageBox.information(
            self,
            "루틴 등록",
            f"'{result.instance.display_name}' 루틴을 비활성 상태로 등록했습니다.",
        )
        return result.instance

    def _load_indicator_follow_rule_mapper(self):
        import importlib.util

        routines_dir = Path(__file__).resolve().parent / "routines"
        mapper_path = next(routines_dir.glob("*/routine_rule_mapper.py"), None)
        if mapper_path is None:
            raise FileNotFoundError(f"routine_rule_mapper.py not found under {routines_dir}")
        spec = importlib.util.spec_from_file_location("indicator_follow_routine_rule_mapper", mapper_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load mapper: {mapper_path}")
        mapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mapper)
        return mapper

    def build_engine_rules_preview_from_current_ui_state(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        ui_state = self.collect_indicator_follow_ui_state()
        try:
            import importlib.util

            mapper_path = Path(__file__).resolve().parent / "routines" / "지표추종매매" / "routine_rule_mapper.py"
            spec = importlib.util.spec_from_file_location("indicator_follow_routine_rule_mapper", mapper_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load mapper: {mapper_path}")
            mapper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mapper)
            return mapper.build_engine_rules_preview_from_ui_state(ui_state, rules)
        except Exception as exc:
            return {
                "preview_rules": deepcopy(rules) if isinstance(rules, dict) else {},
                "mapped_paths": [],
                "warnings": [f"rule mapper preview failed: {exc}"],
            }

    def build_engine_rules_preview_from_current_ui_state(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        ui_state = self.collect_indicator_follow_ui_state()
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            return mapper.build_engine_rules_preview_from_ui_state(ui_state, rules)
        except Exception as exc:
            return {
                "preview_rules": deepcopy(rules) if isinstance(rules, dict) else {},
                "mapped_paths": [],
                "warnings": [f"rule mapper preview failed: {exc}"],
            }

    def build_engine_rules_diff_from_preview(self, engine_rules_preview):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            return mapper.compare_engine_rules_preview(rules, engine_rules_preview)
        except Exception as exc:
            return {
                "changes": [],
                "summary": {
                    "same": 0,
                    "changed": 0,
                    "added": 0,
                    "missing": 0,
                    "postponed": 0,
                },
                "warnings": [f"rule mapper diff failed: {exc}"],
            }

    def build_engine_rules_pending_from_current_ui_state(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        ui_state = self.collect_indicator_follow_ui_state()
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            return mapper.build_engine_rules_pending_from_ui_state(ui_state, rules)
        except Exception as exc:
            pending_rules = deepcopy(rules) if isinstance(rules, dict) else {}
            pending_rules["indicator_follow_rule_pending"] = {
                "version": "0.1",
                "source": "indicator_follow_ui_state",
                "mode": "error",
                "mapped_paths": [],
                "candidates": {},
                "warnings": [f"rule mapper pending failed: {exc}"],
            }
            return {
                "pending_rules": pending_rules,
                "preview_result": {
                    "preview_rules": deepcopy(rules) if isinstance(rules, dict) else {},
                    "mapped_paths": [],
                    "warnings": [f"rule mapper pending failed: {exc}"],
                },
                "warnings": [f"rule mapper pending failed: {exc}"],
            }

    def build_engine_rules_approval_simulation_from_current_ui_state(self, engine_rules_preview=None):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            preview = engine_rules_preview
            if not isinstance(preview, dict):
                ui_state = self.collect_indicator_follow_ui_state()
                preview = mapper.build_engine_rules_preview_from_ui_state(ui_state, rules)

            no_approval = mapper.approve_engine_rule_candidates(rules, preview, [])
            buy_only = mapper.approve_engine_rule_candidates(
                rules,
                preview,
                ["buy.groups[0].conditions"],
            )
            sell_only = mapper.approve_engine_rule_candidates(
                rules,
                preview,
                ["sell.signals.ui_preview_condition_c_macd_sell"],
            )

            buy_added_conditions = []
            if "buy.groups[0].conditions" in buy_only.get("applied_paths", []):
                current_conditions = (
                    rules.get("buy", {})
                    .get("groups", [{}])[0]
                    .get("conditions", [])
                    if isinstance(rules, dict)
                    else []
                )
                simulated_conditions = (
                    buy_only.get("rules", {})
                    .get("buy", {})
                    .get("groups", [{}])[0]
                    .get("conditions", [])
                )
                if isinstance(current_conditions, list) and isinstance(simulated_conditions, list):
                    buy_added_conditions = simulated_conditions[len(current_conditions):]

            sell_signals = sell_only.get("rules", {}).get("sell", {}).get("signals", {})
            current_signals = rules.get("sell", {}).get("signals", {}) if isinstance(rules, dict) else {}
            added_signal = sell_signals.get("ui_condition_c_macd_sell", {})
            return {
                "simulation_only": True,
                "not_saved": True,
                "not_applied": True,
                "no_approval": {
                    "approvals": [],
                    "applied_paths": no_approval.get("applied_paths", []),
                    "skipped_paths": no_approval.get("skipped_paths", []),
                    "warnings": no_approval.get("warnings", []),
                },
                "buy_only": {
                    "approvals": ["buy.groups[0].conditions"],
                    "applied_paths": buy_only.get("applied_paths", []),
                    "skipped_paths": buy_only.get("skipped_paths", []),
                    "warnings": buy_only.get("warnings", []),
                    "added_conditions": buy_added_conditions,
                },
                "sell_only": {
                    "approvals": ["sell.signals.ui_preview_condition_c_macd_sell"],
                    "applied_paths": sell_only.get("applied_paths", []),
                    "skipped_paths": sell_only.get("skipped_paths", []),
                    "warnings": sell_only.get("warnings", []),
                    "added_signal_key": "ui_condition_c_macd_sell" if added_signal else None,
                    "macd_sell_unchanged": (
                        sell_signals.get("macd_sell") == current_signals.get("macd_sell")
                        if isinstance(sell_signals, dict) and isinstance(current_signals, dict)
                        else False
                    ),
                    "enabled": added_signal.get("enabled") if isinstance(added_signal, dict) else None,
                },
            }
        except Exception as exc:
            return {
                "simulation_only": True,
                "not_saved": True,
                "not_applied": True,
                "error": f"rule mapper approval simulation failed: {exc}",
            }

    def _summarize_rule_diff_value(self, value, max_length=240):
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return text if len(text) <= max_length else text[: max_length - 3] + "..."
        return value

    def _summarize_rule_diff_change(self, change):
        risk = str(change.get("risk") or "").lower()
        max_length = 1800 if risk in {"high", "medium"} else 240
        path = change.get("path")
        return {
            "separator": f"----- {path} -----",
            "path": path,
            "status": change.get("status"),
            "risk": change.get("risk"),
            "note": change.get("note"),
            "current_value": self._summarize_rule_diff_value(change.get("current_value"), max_length),
            "preview_value": self._summarize_rule_diff_value(change.get("preview_value"), max_length),
        }

    def _rule_candidate_risk(self, path):
        risks = {
            "buy.groups[0].conditions": "medium",
            "sell.signals.ui_preview_condition_c_macd_sell": "high",
        }
        return risks.get(str(path), "low")

    def _rule_candidate_note(self, path):
        notes = {
            "buy.groups[0].conditions": "Merge add_conditions into buy.groups[0].conditions",
            "sell.signals.ui_preview_condition_c_macd_sell": "Add disabled signal candidate without changing macd_sell",
        }
        return notes.get(str(path), "Rule candidate preview only")

    def _rule_candidate_display_label(self, path):
        labels = {
            "buy.groups[0].conditions": "buy merge conditions",
            "sell.signals.ui_preview_condition_c_macd_sell": "sell add signal candidate",
        }
        return labels.get(str(path), str(path))

    def _clear_rule_approval_controls_layout(self):
        layout = getattr(self, "_rule_approval_controls_layout", None)
        if layout is None or not hasattr(layout, "count"):
            return
        try:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget() if item is not None and hasattr(item, "widget") else None
                if widget is not None and hasattr(widget, "setParent"):
                    widget.setParent(None)
        except TypeError:
            return

    def _refresh_rule_approval_controls(self, session):
        box = getattr(self, "_rule_approval_controls_box", None)
        layout = getattr(self, "_rule_approval_controls_layout", None)
        if box is None or layout is None:
            return

        self._clear_rule_approval_controls_layout()
        self._rule_approval_decision_widgets = {}

        decisions = session.get("decisions", {}) if isinstance(session, dict) else {}
        candidate_types = session.get("candidate_types", {}) if isinstance(session, dict) else {}
        if not isinstance(decisions, dict) or not decisions:
            box.setVisible(False)
            return

        notice = QLabel("미리보기 전용: 선택한 decision은 저장/적용되지 않으며 rules.json을 변경하지 않습니다.")
        if hasattr(notice, "setWordWrap"):
            notice.setWordWrap(True)
        layout.addWidget(notice, 0, 0, 1, 4)

        headers = ["Path", "Type", "Risk", "Decision"]
        for column, header in enumerate(headers):
            layout.addWidget(QLabel(header), 1, column)
        if hasattr(layout, "setColumnStretch"):
            layout.setColumnStretch(0, 1)
        if hasattr(layout, "setColumnMinimumWidth"):
            layout.setColumnMinimumWidth(1, 130)
            layout.setColumnMinimumWidth(2, 70)
            layout.setColumnMinimumWidth(3, 180)

        decision_values = [
            "PENDING",
            "APPROVED",
            "REJECTED",
            "DEFERRED",
            "APPLIED_PREVIEW_ONLY",
        ]
        for row, path in enumerate(decisions.keys(), start=2):
            path_text = str(path)
            candidate_type = str(candidate_types.get(path_text, ""))
            risk = self._rule_candidate_risk(path_text)
            decision = str(decisions.get(path_text) or "PENDING")
            combo = QComboBox()
            combo.addItems(decision_values)
            combo.setCurrentText(decision if decision in decision_values else "PENDING")
            combo.setToolTip(self._rule_candidate_note(path_text))
            if hasattr(combo, "setMinimumWidth"):
                combo.setMinimumWidth(180)
            combo.currentIndexChanged.connect(
                lambda _index=None, p=path_text, widget=combo: self._handle_rule_approval_decision_changed(
                    p,
                    widget.currentText(),
                )
            )

            path_label = QLabel(self._rule_candidate_display_label(path_text))
            path_label.setToolTip(path_text)
            type_label = QLabel(candidate_type)
            risk_label = QLabel(risk)
            if hasattr(type_label, "setMinimumWidth"):
                type_label.setMinimumWidth(130)
            if hasattr(risk_label, "setMinimumWidth"):
                risk_label.setMinimumWidth(70)

            layout.addWidget(path_label, row, 0)
            layout.addWidget(type_label, row, 1)
            layout.addWidget(risk_label, row, 2)
            layout.addWidget(combo, row, 3)
            self._rule_approval_decision_widgets[path_text] = combo

        save_button = QPushButton("승인 검토 상태 저장")
        save_button.setToolTip(
            "현재 승인 검토 상태(decision)만 저장합니다.\nrules.json은 변경되지 않습니다."
        )
        save_button.clicked.connect(self._handle_rule_approval_session_save_clicked)
        save_row = len(decisions) + 2
        layout.addWidget(save_button, save_row, 3)
        self._rule_approval_save_button = save_button

        box.setVisible(True)

    def _build_rule_pipeline_from_session(self, session):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        mapper = self._load_indicator_follow_rule_mapper()
        preview = getattr(self, "_last_rule_engine_preview", {})
        validation = mapper.validate_rule_approval_session_for_preview(session, rules, preview)
        pipeline_preview = mapper.build_rule_pipeline_preview(rules, preview, session)
        pipeline_preview["approval_session_validation"] = validation
        self._update_rule_approval_session_dirty_state(session)
        pipeline_preview["approval_session_file"] = self._approval_session_file_status_with_dirty()
        pipeline_preview["commit_preview"] = mapper.build_rule_commit_preview(
            rules,
            preview,
            session,
            {"approval_session_dirty": self._rule_approval_session_dirty},
        )
        return pipeline_preview

    def _rule_approval_decisions_snapshot(self, session):
        if not isinstance(session, dict):
            return {}
        decisions = session.get("decisions", {})
        if not isinstance(decisions, dict):
            return {}
        return deepcopy(decisions)

    def _reset_rule_approval_session_dirty_baseline(self, session, has_saved_baseline):
        if has_saved_baseline:
            self._last_saved_rule_approval_session_decisions = self._rule_approval_decisions_snapshot(session)
        else:
            self._last_saved_rule_approval_session_decisions = None
        self._rule_approval_session_dirty = False

    def _update_rule_approval_session_dirty_state(self, session):
        baseline = getattr(self, "_last_saved_rule_approval_session_decisions", None)
        if isinstance(baseline, dict):
            self._rule_approval_session_dirty = self._rule_approval_decisions_snapshot(session) != baseline
        else:
            self._rule_approval_session_dirty = False
        return self._rule_approval_session_dirty

    def _approval_session_file_status_with_dirty(self, status=None):
        view = deepcopy(status) if isinstance(status, dict) else deepcopy(
            getattr(self, "_rule_approval_file_status", {})
        )
        dirty = bool(getattr(self, "_rule_approval_session_dirty", False))
        view["dirty"] = dirty
        view["dirty_reason"] = (
            "decision changed after last session restore/save" if dirty else None
        )
        save_result = getattr(self, "_last_rule_approval_session_save_result", {})
        if isinstance(save_result, dict) and save_result:
            view["saved"] = save_result.get("saved") is True
            if save_result.get("saved") is True:
                view["status"] = "SAVED"
                saved_session = save_result.get("session", {})
                if isinstance(saved_session, dict):
                    view["saved_at"] = saved_session.get("saved_at")
                    fingerprint = saved_session.get("fingerprint")
                    if isinstance(fingerprint, str) and len(fingerprint) > 12:
                        view["fingerprint"] = f"{fingerprint[:12]}..."
                    elif isinstance(fingerprint, str):
                        view["fingerprint"] = fingerprint
            warnings = view.get("warnings", [])
            if not isinstance(warnings, list):
                warnings = []
            for source in (save_result.get("warnings"), save_result.get("blocked_reasons")):
                if isinstance(source, list):
                    warnings.extend(source)
            view["warnings"] = warnings
        return view

    def _approval_session_validation_display_view(self, validation, session=None):
        validation = validation if isinstance(validation, dict) else {}
        session = session if isinstance(session, dict) else {}
        valid = validation.get("valid") is True
        warnings = []
        for source in (validation.get("warnings"), session.get("warnings")):
            if isinstance(source, list):
                warnings.extend(source)
        return {
            "status": "VALID" if valid else "MISMATCH",
            "path_match": validation.get("path_match"),
            "type_match": validation.get("type_match"),
            "fingerprint_match": validation.get("fingerprint_match"),
            "restore_status": session.get("restore_status", "NEW"),
            "blocked_reasons": validation.get("blocked_reasons", []),
            "warnings": warnings,
        }

    def _approval_session_display_view(self, session):
        session_view = deepcopy(session) if isinstance(session, dict) else {}
        fingerprint = session_view.get("fingerprint")
        if isinstance(fingerprint, str) and len(fingerprint) > 12:
            session_view["fingerprint"] = f"{fingerprint[:12]}..."
        session_view.pop("fingerprint_detail", None)
        session_view.pop("validation", None)
        return session_view

    def _rule_approval_session_path(self):
        path = getattr(self, "_approval_session_path", None)
        if path:
            return Path(path)
        return self._default_rule_approval_session_path()

    def _approval_session_file_display_view(self, file_result=None, restore_result=None):
        path = self._rule_approval_session_path()
        file_result = file_result if isinstance(file_result, dict) else {}
        restore_result = restore_result if isinstance(restore_result, dict) else {}
        warnings = []
        for source in (file_result.get("warnings"), restore_result.get("warnings")):
            if isinstance(source, list):
                warnings.extend(source)
        if file_result.get("blocked_reasons"):
            warnings.extend(file_result.get("blocked_reasons", []))
        status = "NOT_FOUND"
        if file_result.get("exists") is True:
            status = "LOADED"
        if file_result and file_result.get("ok") is False:
            status = "CORRUPTED"
        return {
            "status": status,
            "restore_status": restore_result.get("restore_status", "NEW"),
            "session_path": str(path),
            "saved": False,
            "dirty": bool(getattr(self, "_rule_approval_session_dirty", False)),
            "dirty_reason": (
                "decision changed after last session restore/save"
                if getattr(self, "_rule_approval_session_dirty", False)
                else None
            ),
            "warnings": warnings,
        }

    def _rule_pipeline_display_view(self, pipeline_preview):
        apply_preview = pipeline_preview.get("apply_preview", {}) if isinstance(pipeline_preview, dict) else {}
        session = pipeline_preview.get("session", {}) if isinstance(pipeline_preview, dict) else {}
        validation = (
            pipeline_preview.get("approval_session_validation", {})
            if isinstance(pipeline_preview, dict)
            else {}
        )
        commit_preview = (
            pipeline_preview.get("commit_preview", {})
            if isinstance(pipeline_preview, dict)
            else {}
        )
        return {
            "approval_session_file": (
                pipeline_preview.get("approval_session_file", {})
                if isinstance(pipeline_preview, dict)
                else {}
            ),
            "approval_session_validation": self._approval_session_validation_display_view(validation, session),
            "session": self._approval_session_display_view(session),
            "approval_result": pipeline_preview.get("approval_result", {}) if isinstance(pipeline_preview, dict) else {},
            "patch_preview": pipeline_preview.get("patch_preview", {}) if isinstance(pipeline_preview, dict) else {},
            "apply_preview": {
                "mode": apply_preview.get("mode"),
                "stage": apply_preview.get("stage"),
                "summary": apply_preview.get("summary", {}),
                "applied_patches": apply_preview.get("applied_patches", []),
                "skipped_patches": apply_preview.get("skipped_patches", []),
                "warnings": apply_preview.get("warnings", []),
            },
            "commit_preview": {
                "mode": commit_preview.get("mode"),
                "stage": commit_preview.get("stage"),
                "commit_allowed": commit_preview.get("commit_allowed"),
                "blocked_reasons": commit_preview.get("blocked_reasons", []),
                "apply_preview_summary": commit_preview.get("apply_preview_summary", {}),
                "final_diff": commit_preview.get("final_diff", []),
                "safety_checks": commit_preview.get("safety_checks", {}),
                "warnings": commit_preview.get("warnings", []),
            },
        }

    def build_rule_candidate_approval_and_patch_preview(
        self,
        engine_rules_preview=None,
        approval_decisions=None,
        saved_session=None,
    ):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            preview = engine_rules_preview
            if not isinstance(preview, dict):
                ui_state = self.collect_indicator_follow_ui_state()
                preview = mapper.build_engine_rules_preview_from_ui_state(ui_state, rules)

            decisions = approval_decisions if isinstance(approval_decisions, dict) else {}
            file_result = {}
            restore_result = {}
            if isinstance(saved_session, dict):
                restore_result = rule_approval_session_file_service.restore_saved_rule_approval_session(
                    saved_session,
                    rules,
                    preview,
                )
                session = restore_result.get("session") if restore_result.get("ok") else None
                if not isinstance(session, dict):
                    session = mapper.build_rule_approval_session(preview, initial_decisions=decisions)
                file_result = {
                    "ok": True,
                    "exists": True,
                    "session_path": "injected",
                    "warnings": [],
                    "blocked_reasons": [],
                }
            else:
                file_result = rule_approval_session_file_service.load_rule_approval_session(
                    self._rule_approval_session_path()
                )
                loaded_session = file_result.get("session") if file_result.get("ok") else None
                if file_result.get("exists") is True and isinstance(loaded_session, dict):
                    restore_result = rule_approval_session_file_service.restore_saved_rule_approval_session(
                        loaded_session,
                        rules,
                        preview,
                    )
                    session = restore_result.get("session") if restore_result.get("ok") else None
                else:
                    session = None
                if not isinstance(session, dict):
                    session = mapper.build_rule_approval_session(preview, initial_decisions=decisions)
                    fingerprint = mapper.build_rule_approval_session_fingerprint(rules, preview)
                    session["fingerprint"] = fingerprint.get("fingerprint")
                    session["fingerprint_detail"] = fingerprint
                    session["restore_status"] = "NEW"
            validation = mapper.validate_rule_approval_session_for_preview(session, rules, preview)
            self._last_rule_engine_preview = deepcopy(preview)
            self._last_rule_approval_session_save_result = {}
            restore_status = session.get("restore_status", restore_result.get("restore_status", "NEW"))
            self._reset_rule_approval_session_dirty_baseline(
                session,
                restore_status in {"RESTORED", "RESET_TO_PENDING"},
            )
            pipeline_preview = mapper.build_rule_pipeline_preview(
                rules,
                preview,
                session,
            )
            pipeline_preview["approval_session_validation"] = validation
            pipeline_preview["approval_session_file"] = self._approval_session_file_display_view(
                file_result,
                restore_result,
            )
            pipeline_preview["commit_preview"] = mapper.build_rule_commit_preview(
                rules,
                preview,
                session,
                {"approval_session_dirty": self._rule_approval_session_dirty},
            )
            self._rule_approval_session = deepcopy(pipeline_preview.get("session", session))
            self._rule_approval_session_validation = deepcopy(validation)
            self._rule_approval_file_status = deepcopy(pipeline_preview["approval_session_file"])
            self._rule_approval_restore_result = deepcopy(restore_result)
            self._loaded_rule_approval_session = deepcopy(
                file_result.get("session") if isinstance(file_result, dict) else None
            )
            self._last_rule_pipeline_preview = deepcopy(pipeline_preview)
            return self._rule_pipeline_display_view(pipeline_preview)
        except Exception as exc:
            return {
                "approval_session_file": {
                    "status": "CORRUPTED",
                    "restore_status": "ERROR",
                    "session_path": str(self._rule_approval_session_path()),
                    "warnings": [f"approval session file load failed: {exc}"],
                },
                "approval_session_validation": {
                    "status": "ERROR",
                    "path_match": False,
                    "type_match": False,
                    "fingerprint_match": False,
                    "restore_status": "ERROR",
                    "blocked_reasons": [f"rule approval session validation failed: {exc}"],
                    "warnings": [],
                },
                "session": {
                    "mode": "approval_session",
                    "session_status": "ERROR",
                    "decisions": {},
                    "candidate_types": {},
                    "updated_at": "",
                    "warnings": [f"rule approval session failed: {exc}"],
                },
                "approval_result": {
                    "mode": "candidate_approval",
                    "status": "ERROR",
                    "approved_paths": [],
                    "rejected_paths": [],
                    "deferred_paths": [],
                    "candidate_decisions": {},
                    "warnings": [f"rule candidate approval failed: {exc}"],
                },
                "patch_preview": {
                    "mode": "approved_rule_patch_preview",
                    "stage": "RULE_PATCH_PREVIEW",
                    "patches": [],
                    "summary": {
                        "approved": 0,
                        "patches": 0,
                        "skipped": 0,
                    },
                    "skipped_paths": [],
                    "warnings": [f"approved rule patch preview failed: {exc}"],
                },
                "apply_preview": {
                    "mode": "approved_rule_apply_preview",
                    "stage": "RULE_APPLY_PREVIEW",
                    "summary": {
                        "patches": 0,
                        "applied": 0,
                        "skipped": 0,
                    },
                    "applied_patches": [],
                    "skipped_patches": [],
                    "warnings": [f"approved rule apply preview failed: {exc}"],
                },
                "commit_preview": {
                    "mode": "rule_commit_preview",
                    "stage": "RULE_COMMIT_PREVIEW",
                    "commit_allowed": False,
                    "blocked_reasons": [f"rule commit preview failed: {exc}"],
                    "apply_preview_summary": {},
                    "final_diff": [],
                    "safety_checks": {
                        "rules_json_write": False,
                        "engine_connected": False,
                        "buy_groups_replace": False,
                        "macd_sell_replace": False,
                    },
                    "warnings": [],
                },
            }

    def _blocked_rule_approval_session_save_result(self, reason):
        return {
            "ok": False,
            "saved": False,
            "stage": "approval_session_save_blocked",
            "session_path": str(self._rule_approval_session_path()),
            "session": None,
            "blocked_reasons": [reason],
            "warnings": [],
        }

    def _rule_approval_session_save_allowed(self, session, validation):
        if not isinstance(session, dict) or not session.get("decisions"):
            return False, "approval session is required"
        if not self._rule_approval_session_path():
            return False, "approval session path is required"
        file_status = getattr(self, "_rule_approval_file_status", {})
        if isinstance(file_status, dict) and file_status.get("status") == "CORRUPTED":
            return False, "approval session file is corrupted"
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            return False, "approval session validation must be VALID"
        if validation.get("path_match") is not True:
            return False, "approval session path_match must be true"
        if validation.get("type_match") is not True:
            return False, "approval session type_match must be true"
        if validation.get("fingerprint_match") is not True:
            return False, "approval session fingerprint_match must be true"
        return True, ""

    def _handle_rule_approval_session_save_clicked(self):
        session = getattr(self, "_rule_approval_session", {})
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        preview = getattr(self, "_last_rule_engine_preview", {})
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            validation = mapper.validate_rule_approval_session_for_preview(session, rules, preview)
            allowed, reason = self._rule_approval_session_save_allowed(session, validation)
            if allowed:
                save_result = rule_approval_session_file_service.save_rule_approval_session(
                    session,
                    self._rule_approval_session_path(),
                )
                if save_result.get("saved") is True:
                    self._reset_rule_approval_session_dirty_baseline(session, has_saved_baseline=True)
                else:
                    self._update_rule_approval_session_dirty_state(session)
            else:
                save_result = self._blocked_rule_approval_session_save_result(reason)
                self._update_rule_approval_session_dirty_state(session)
            self._last_rule_approval_session_save_result = deepcopy(save_result)
            self._rule_approval_session_validation = deepcopy(validation)
            pipeline_preview = self._build_rule_pipeline_from_session(session)
            self._rule_approval_session = deepcopy(pipeline_preview.get("session", session))
            self._rule_approval_file_status = deepcopy(
                pipeline_preview.get("approval_session_file", {})
            )
            self._last_rule_pipeline_preview = deepcopy(pipeline_preview)
            display_view = self._rule_pipeline_display_view(pipeline_preview)
            self._refresh_rule_approval_controls(display_view.get("session", {}))
            self._render_rule_validation_preview(display_view)
            return display_view
        except Exception as exc:
            save_result = self._blocked_rule_approval_session_save_result(
                f"approval session save failed: {exc}"
            )
            self._last_rule_approval_session_save_result = deepcopy(save_result)
            pipeline_preview = self._build_rule_pipeline_from_session(session)
            display_view = self._rule_pipeline_display_view(pipeline_preview)
            self._render_rule_validation_preview(display_view)
            return display_view

    def _handle_rule_approval_decision_changed(self, path, decision):
        try:
            mapper = self._load_indicator_follow_rule_mapper()
            session = mapper.update_rule_approval_session(
                getattr(self, "_rule_approval_session", {}),
                path,
                decision,
            )
            pipeline_preview = self._build_rule_pipeline_from_session(session)
            self._rule_approval_session = deepcopy(pipeline_preview.get("session", session))
            self._rule_approval_session_validation = deepcopy(
                pipeline_preview.get("approval_session_validation", {})
            )
            self._last_rule_pipeline_preview = deepcopy(pipeline_preview)
            display_view = self._rule_pipeline_display_view(pipeline_preview)
            self._refresh_rule_approval_controls(display_view.get("session", {}))
            self._render_rule_validation_preview(display_view)
            return display_view
        except Exception as exc:
            self.preview_text.setPlainText(f"Rule approval preview update failed: {exc}")
            self.preview_text.setVisible(True)
            return None

    def _render_rule_validation_preview(self, approval_and_patch_preview=None):
        context = getattr(self, "_last_rule_validation_context", {})
        if not isinstance(context, dict):
            context = {}
        approval_and_patch_preview = (
            approval_and_patch_preview if isinstance(approval_and_patch_preview, dict) else {}
        )
        preview = "\n".join(
            [
                "UI State Preview",
                "",
                "[Validation Summary]",
                *context.get("summary_lines", []),
                "",
                "[Collected UI JSON]",
                json.dumps(context.get("state", {}), ensure_ascii=False, indent=2),
                "",
                "[Pending Rules Preview]",
                json.dumps(context.get("rules_preview_view", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Mapper Preview]",
                json.dumps(context.get("engine_rules_preview_view", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Mapper Pending]",
                json.dumps(context.get("engine_rules_pending_view", {}), ensure_ascii=False, indent=2),
                "",
                "[Saved Rule Mapper Pending]",
                json.dumps(context.get("saved_engine_rules_pending_view", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Mapper Approval Simulation - Not Saved]",
                json.dumps(context.get("engine_rules_approval_simulation", {}), ensure_ascii=False, indent=2),
                "",
                "[Approval Session File]",
                json.dumps(
                    approval_and_patch_preview.get("approval_session_file", {}),
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "[Approval Session Validation]",
                json.dumps(
                    approval_and_patch_preview.get("approval_session_validation", {}),
                    ensure_ascii=False,
                    indent=2,
                ),
                "",
                "[Rule Approval Session]",
                json.dumps(approval_and_patch_preview.get("session", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Candidate Approval]",
                json.dumps(approval_and_patch_preview.get("approval_result", {}), ensure_ascii=False, indent=2),
                "",
                "[Approved Rule Patch Preview]",
                json.dumps(approval_and_patch_preview.get("patch_preview", {}), ensure_ascii=False, indent=2),
                "",
                "[Approved Rule Apply Preview]",
                json.dumps(approval_and_patch_preview.get("apply_preview", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Commit Preview]",
                json.dumps(approval_and_patch_preview.get("commit_preview", {}), ensure_ascii=False, indent=2),
                "",
                "[Rule Mapper Diff]",
                json.dumps(context.get("engine_rules_diff_view", {}), ensure_ascii=False, indent=2),
            ]
        )
        self.preview_text.setPlainText(preview)
        self.preview_text.setVisible(True)

    def _get_saved_rule_mapper_pending_from_rules(self):
        rules = getattr(self, "rules", None)
        if not isinstance(rules, dict):
            rules = getattr(self, "rules_data", {})
        pending = rules.get("indicator_follow_rule_pending") if isinstance(rules, dict) else None
        return pending if isinstance(pending, dict) else None

    def _compare_saved_rule_mapper_pending(self, current_pending, saved_pending):
        if not isinstance(saved_pending, dict):
            return {
                "saved_exists": False,
                "matches_current": False,
                "checks": {
                    "source_ui_state_hash": "not_available",
                    "mapped_paths": False,
                    "candidates": False,
                    "warnings": False,
                },
                "warning": None,
            }

        current = current_pending if isinstance(current_pending, dict) else {}

        def normalized_json(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)

        current_hash = current.get("source_ui_state_hash")
        saved_hash = saved_pending.get("source_ui_state_hash")
        hash_check = (
            current_hash == saved_hash
            if current_hash and saved_hash
            else "not_available"
        )
        checks = {
            "source_ui_state_hash": hash_check,
            "mapped_paths": current.get("mapped_paths") == saved_pending.get("mapped_paths"),
            "candidates": normalized_json(current.get("candidates", {}))
            == normalized_json(saved_pending.get("candidates", {})),
            "warnings": current.get("warnings", []) == saved_pending.get("warnings", []),
        }
        matches_current = all(
            value is True for value in checks.values()
            if value != "not_available"
        )
        warning = None
        if not matches_current:
            warning = (
                "saved Rule Mapper pending differs from current UI pending; "
                "do not approve without revalidation"
            )
        return {
            "saved_exists": True,
            "matches_current": matches_current,
            "checks": checks,
            "warning": warning,
        }

    def _build_saved_rule_mapper_pending_view(self, current_pending):
        saved_pending = self._get_saved_rule_mapper_pending_from_rules()
        comparison = self._compare_saved_rule_mapper_pending(current_pending, saved_pending)
        if not isinstance(saved_pending, dict):
            return comparison

        comparison["saved_summary"] = {
            "version": saved_pending.get("version"),
            "source": saved_pending.get("source"),
            "source_ui_state_hash": saved_pending.get("source_ui_state_hash"),
            "mode": saved_pending.get("mode"),
            "mapped_paths": saved_pending.get("mapped_paths", []),
            "candidate_keys": sorted(
                saved_pending.get("candidates", {}).keys()
            ) if isinstance(saved_pending.get("candidates"), dict) else [],
            "warnings_count": len(saved_pending.get("warnings", []))
            if isinstance(saved_pending.get("warnings"), list) else 0,
        }
        return comparison

    def save_indicator_follow_ui_state_to_rules(self):
        core_keys = [
            "buy",
            "sell",
            "indicators",
            "order_policy",
            "cancel_policy",
            "safety",
        ]
        result = {
            "success": False,
            "path": str(self.rules_path),
            "saved_namespace": "indicator_follow_ui_state",
            "core_keys_unchanged": {},
            "error": None,
        }
        tmp_path = self.rules_path.with_name(f"{self.rules_path.name}.tmp")

        try:
            if not self.rules_path.exists():
                raise FileNotFoundError(f"rules.json not found: {self.rules_path}")

            with self.rules_path.open("r", encoding="utf-8") as f:
                current_rules = json.load(f)
            if not isinstance(current_rules, dict):
                raise ValueError("rules.json root must be an object")

            before_core = {key: deepcopy(current_rules.get(key)) for key in core_keys}
            rules_copy = deepcopy(current_rules)
            rules_copy["indicator_follow_ui_state"] = {
                "ui_state_version": "0.1",
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "state": self.collect_indicator_follow_ui_state(),
            }
            after_core = {key: deepcopy(rules_copy.get(key)) for key in core_keys}
            result["core_keys_unchanged"] = {
                key: before_core.get(key) == after_core.get(key)
                for key in core_keys
            }
            if not all(result["core_keys_unchanged"].values()):
                raise ValueError("core rules changed unexpectedly")

            serialized = json.dumps(rules_copy, ensure_ascii=False, indent=2) + "\n"
            tmp_path.write_text(serialized, encoding="utf-8")
            tmp_path.replace(self.rules_path)

            with self.rules_path.open("r", encoding="utf-8") as f:
                saved_rules = json.load(f)
            saved_core = {key: deepcopy(saved_rules.get(key)) for key in core_keys}
            result["core_keys_unchanged"] = {
                key: before_core.get(key) == saved_core.get(key)
                for key in core_keys
            }
            if not isinstance(saved_rules.get("indicator_follow_ui_state"), dict):
                raise ValueError("indicator_follow_ui_state was not saved")
            if not all(result["core_keys_unchanged"].values()):
                raise ValueError("core rules changed after save")

            self.rules_data = saved_rules
            self.rules = saved_rules
            result["success"] = True
        except Exception as exc:
            result["error"] = str(exc)
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

        self._last_ui_state_save_result = result
        status = "SUCCESS" if result["success"] else "FAILED"
        self.preview_text.setPlainText(
            json.dumps(
                {
                    "save_indicator_follow_ui_state": status,
                    "path": result["path"],
                    "saved_namespace": result["saved_namespace"],
                    "core_keys_unchanged": result["core_keys_unchanged"],
                    "error": result["error"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        self.preview_text.setVisible(True)
        return result

    def save_indicator_follow_rule_pending_to_rules(self):
        core_keys = [
            "bar",
            "buy",
            "sell",
            "indicators",
            "order_policy",
            "cancel_policy",
            "safety",
            "indicator_follow_ui_state",
        ]
        result = {
            "success": False,
            "path": str(self.rules_path),
            "saved_namespace": "indicator_follow_rule_pending",
            "core_keys_unchanged": {},
            "error": None,
        }
        tmp_path = self.rules_path.with_name(f"{self.rules_path.name}.tmp")

        try:
            if not self.rules_path.exists():
                raise FileNotFoundError(f"rules.json not found: {self.rules_path}")

            pending_result = self.build_engine_rules_pending_from_current_ui_state()
            pending_rules = pending_result.get("pending_rules", {})
            pending = pending_rules.get("indicator_follow_rule_pending")
            if not isinstance(pending, dict):
                raise ValueError("indicator_follow_rule_pending was not generated")
            if pending.get("mode") == "error":
                raise ValueError("indicator_follow_rule_pending generation failed")

            with self.rules_path.open("r", encoding="utf-8") as f:
                current_rules = json.load(f)
            if not isinstance(current_rules, dict):
                raise ValueError("rules.json root must be an object")

            before_core = {key: deepcopy(current_rules.get(key)) for key in core_keys}
            rules_copy = deepcopy(current_rules)
            rules_copy["indicator_follow_rule_pending"] = deepcopy(pending)
            after_core = {key: deepcopy(rules_copy.get(key)) for key in core_keys}
            result["core_keys_unchanged"] = {
                key: before_core.get(key) == after_core.get(key)
                for key in core_keys
            }
            if not all(result["core_keys_unchanged"].values()):
                raise ValueError("core rules changed unexpectedly")

            serialized = json.dumps(rules_copy, ensure_ascii=False, indent=2) + "\n"
            tmp_path.write_text(serialized, encoding="utf-8")
            tmp_path.replace(self.rules_path)

            with self.rules_path.open("r", encoding="utf-8") as f:
                saved_rules = json.load(f)
            saved_core = {key: deepcopy(saved_rules.get(key)) for key in core_keys}
            result["core_keys_unchanged"] = {
                key: before_core.get(key) == saved_core.get(key)
                for key in core_keys
            }
            if not isinstance(saved_rules.get("indicator_follow_rule_pending"), dict):
                raise ValueError("indicator_follow_rule_pending was not saved")
            if not all(result["core_keys_unchanged"].values()):
                raise ValueError("core rules changed after save")

            self.rules_data = saved_rules
            self.rules = saved_rules
            result["success"] = True
        except Exception as exc:
            result["error"] = str(exc)
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

        self._last_rule_pending_save_result = result
        return result

    def _handle_validate_clicked(self):
        summary_lines = [
            f"signal: {self.validation_signal_line.text()}",
            f"execution: {self.validation_execution_line.text()}",
            f"sell: {self.validation_sell_line.text()}",
            f"buy: {self.validation_buy_line.text()}",
        ]
        state = self.collect_indicator_follow_ui_state()
        rules_preview = self.build_rules_with_indicator_follow_ui_state()
        engine_rules_preview = self.build_engine_rules_preview_from_current_ui_state()
        engine_rules_pending = self.build_engine_rules_pending_from_current_ui_state()
        engine_rules_approval_simulation = (
            self.build_engine_rules_approval_simulation_from_current_ui_state(engine_rules_preview)
        )
        approval_and_patch_preview = self.build_rule_candidate_approval_and_patch_preview(engine_rules_preview)
        engine_rules_diff = self.build_engine_rules_diff_from_preview(engine_rules_preview)
        rules_preview_view = {
            "top_level_keys": list(rules_preview.keys()),
            "indicator_follow_ui_state": rules_preview.get("indicator_follow_ui_state", {}),
        }
        engine_rules_preview_view = {
            "mapped_paths": engine_rules_preview.get("mapped_paths", []),
            "warnings": engine_rules_preview.get("warnings", []),
            "preview_rules": {
                "bar": engine_rules_preview.get("preview_rules", {}).get("bar", {}),
                "buy": engine_rules_preview.get("preview_rules", {}).get("buy", {}),
                "sell": engine_rules_preview.get("preview_rules", {}).get("sell", {}),
                "indicator_follow_rule_preview": (
                    engine_rules_preview.get("preview_rules", {}).get("indicator_follow_rule_preview", {})
                ),
            },
        }
        pending_rules = engine_rules_pending.get("pending_rules", {})
        engine_rules_pending_view = {
            "indicator_follow_rule_pending": pending_rules.get("indicator_follow_rule_pending", {}),
            "warnings": engine_rules_pending.get("warnings", []),
        }
        saved_engine_rules_pending_view = self._build_saved_rule_mapper_pending_view(
            pending_rules.get("indicator_follow_rule_pending", {})
        )
        engine_rules_diff_view = {
            "summary": engine_rules_diff.get("summary", {}),
            "changes": [
                self._summarize_rule_diff_change(change)
                for change in engine_rules_diff.get("changes", [])
            ],
            "warnings": engine_rules_diff.get("warnings", []),
        }
        self._last_rule_validation_context = {
            "summary_lines": summary_lines,
            "state": state,
            "rules_preview_view": rules_preview_view,
            "engine_rules_preview_view": engine_rules_preview_view,
            "engine_rules_pending_view": engine_rules_pending_view,
            "saved_engine_rules_pending_view": saved_engine_rules_pending_view,
            "engine_rules_approval_simulation": engine_rules_approval_simulation,
            "engine_rules_diff_view": engine_rules_diff_view,
        }
        self._refresh_rule_approval_controls(approval_and_patch_preview.get("session", {}))
        self._render_rule_validation_preview(approval_and_patch_preview)

    def _read_ui_widget_value(self, name):
        widget = getattr(self, name, None)
        if widget is None:
            return None
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return None

    def _collect_named_ui_values(self, names):
        values = {}
        for name in names:
            value = self._read_ui_widget_value(name)
            if value is not None:
                values[name] = value
        return values

    def _collect_named_ui_values_without_prefix(self, names, prefix):
        values = {}
        for name in names:
            value = self._read_ui_widget_value(name)
            if value is not None:
                values[name[len(prefix):] if name.startswith(prefix) else name] = value
        return values

    def _collect_prefixed_ui_values(self, prefixes, excluded_names=None):
        excluded = set(excluded_names or ())
        values = {}
        for name in sorted(vars(self)):
            if name in excluded:
                continue
            if any(name.startswith(prefix) for prefix in prefixes):
                value = self._read_ui_widget_value(name)
                if value is not None:
                    values[name] = value
        return values

    def _collect_prefixed_ui_values_without_prefix(self, prefix, excluded_names=None):
        excluded = set(excluded_names or ())
        values = {}
        for name in sorted(vars(self)):
            if name in excluded or not name.startswith(prefix):
                continue
            value = self._read_ui_widget_value(name)
            if value is not None:
                values[name[len(prefix):]] = value
        return values

    def _apply_ui_widget_value(self, name, value):
        widget = getattr(self, name, None)
        if widget is None:
            return {"name": name, "reason": "missing_widget"}
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return None
        if isinstance(widget, QComboBox):
            index = widget.findText(str(value))
            if index < 0:
                return {
                    "name": name,
                    "reason": "combo_value_not_found",
                    "value": value,
                }
            widget.setCurrentIndex(index)
            return None
        if isinstance(widget, QLineEdit):
            widget.setText("" if value is None else str(value))
            return None
        return {
            "name": name,
            "reason": "unsupported_widget",
            "type": type(widget).__name__,
        }

    def _apply_named_ui_values(self, values, prefix=None, result=None):
        result = result if result is not None else {"applied": [], "skipped": []}
        if not isinstance(values, dict):
            result["skipped"].append({
                "name": prefix or "",
                "reason": "values_not_dict",
            })
            return result

        pending = []
        for key, value in values.items():
            name = f"{prefix}{key}" if prefix else key
            pending.append((name, value))

        def apply_order(item):
            widget = getattr(self, item[0], None)
            if isinstance(widget, QComboBox):
                return 0
            if isinstance(widget, QLineEdit):
                return 1
            if isinstance(widget, QCheckBox):
                return 2
            return 3

        for name, value in sorted(pending, key=apply_order):
            skipped = self._apply_ui_widget_value(name, value)
            if skipped is None:
                result["applied"].append(name)
            else:
                result["skipped"].append(skipped)
        return result

    def _apply_prefixed_ui_values(self, values, prefix, result=None):
        return self._apply_named_ui_values(values, prefix=prefix, result=result)

    def _apply_existing_prefixed_ui_values(self, values, prefixes, result=None):
        result = result if result is not None else {"applied": [], "skipped": []}
        if not isinstance(values, dict):
            result["skipped"].append({
                "name": ",".join(prefixes),
                "reason": "values_not_dict",
            })
            return result

        for key, value in values.items():
            candidates = []
            if hasattr(self, key):
                candidates.append(key)
            candidates.extend(f"{prefix}{key}" for prefix in prefixes)
            name = next((candidate for candidate in candidates if hasattr(self, candidate)), None)
            if name is None:
                result["skipped"].append({
                    "name": str(key),
                    "reason": "missing_widget",
                })
                continue
            skipped = self._apply_ui_widget_value(name, value)
            if skipped is None:
                result["applied"].append(name)
            else:
                result["skipped"].append(skipped)
        return result

    def _default_buy_composite_ui_state(self):
        return {
            "enabled": False,
            "logic": "OR",
            "include_unreferenced_active_filters": "AND_REQUIRED",
            "groups": [
                {
                    "enabled": True,
                    "logic": "AND",
                    "filters": ["rsi", "moving_average"],
                },
                {
                    "enabled": True,
                    "logic": "AND",
                    "filters": ["bollinger", "ocr"],
                },
            ],
        }

    def _buy_composite_filter_names(self):
        return ["rsi", "moving_average", "price_compare", "bollinger", "ocr"]

    def _set_buy_composite_warning(self, message):
        label = getattr(self, "buy_composite_warning_label", None)
        if label is None:
            return
        if hasattr(label, "setText"):
            label.setText(message or "")
        if hasattr(label, "setVisible"):
            label.setVisible(bool(message))

    def _collect_buy_composite_ui_state(self):
        groups = []
        for group_index in (1, 2):
            filters = []
            for filter_name in self._buy_composite_filter_names():
                check_name = f"buy_composite_group_{group_index}_{filter_name}_check"
                if self._read_ui_widget_value(check_name) is True:
                    filters.append(filter_name)
            groups.append({
                "enabled": bool(self._read_ui_widget_value(f"buy_composite_group_{group_index}_enabled_check")),
                "logic": str(self._read_ui_widget_value(f"buy_composite_group_{group_index}_logic_combo") or "AND"),
                "filters": filters,
            })

        return {
            "enabled": bool(self._read_ui_widget_value("buy_composite_enabled_check")),
            "logic": str(self._read_ui_widget_value("buy_composite_logic_combo") or "OR"),
            "include_unreferenced_active_filters": "AND_REQUIRED",
            "groups": groups,
        }

    def _apply_buy_composite_ui_state(self, values, result=None):
        result = result if result is not None else {"applied": [], "skipped": []}
        state = deepcopy(values) if isinstance(values, dict) else self._default_buy_composite_ui_state()
        groups = state.get("groups")
        if not isinstance(groups, list):
            groups = self._default_buy_composite_ui_state()["groups"]

        if len(groups) > 2:
            message = (
                "Composite setting exceeds the UI-supported group count. "
                "Existing settings are preserved, but this screen cannot edit them."
            )
            self._set_buy_composite_warning(message)
            result["skipped"].append({
                "name": "buy_ui.signal_filter.buy_composite",
                "reason": "unsupported_group_count",
                "groups": len(groups),
            })
            for name in vars(self):
                if name.startswith("buy_composite_"):
                    widget = getattr(self, name, None)
                    if hasattr(widget, "setEnabled"):
                        widget.setEnabled(False)
            return result

        self._set_buy_composite_warning("")
        self._apply_ui_widget_value("buy_composite_enabled_check", bool(state.get("enabled", False)))
        self._apply_ui_widget_value("buy_composite_logic_combo", state.get("logic", "OR"))
        self._apply_ui_widget_value("buy_composite_include_unreferenced_combo", "AND_REQUIRED")

        default_groups = self._default_buy_composite_ui_state()["groups"]
        for group_index in (1, 2):
            group = groups[group_index - 1] if group_index <= len(groups) and isinstance(groups[group_index - 1], dict) else default_groups[group_index - 1]
            self._apply_ui_widget_value(
                f"buy_composite_group_{group_index}_enabled_check",
                bool(group.get("enabled", False)),
            )
            self._apply_ui_widget_value(
                f"buy_composite_group_{group_index}_logic_combo",
                group.get("logic", "AND"),
            )

            selected_filters = set()
            for filter_name in group.get("filters", []):
                if filter_name in self._buy_composite_filter_names():
                    selected_filters.add(filter_name)
                else:
                    result["skipped"].append({
                        "name": f"buy_ui.signal_filter.buy_composite.groups[{group_index - 1}].filters",
                        "reason": "unknown_filter",
                        "value": filter_name,
                    })

            for filter_name in self._buy_composite_filter_names():
                self._apply_ui_widget_value(
                    f"buy_composite_group_{group_index}_{filter_name}_check",
                    filter_name in selected_filters,
                )

        if hasattr(self, "_sync_buy_composite_control_states"):
            self._sync_buy_composite_control_states()
        return result

    def _sync_indicator_follow_ui_after_apply(self):
        errors = []
        sync_names = [
            "_update_all_buy_method_states",
            "_update_hoga_total",
            "_update_hoga_mode",
            "_update_time_mode",
            "_update_apply_all_enabled",
            "_update_additional_active_state",
            "_update_situation_response_state",
        ]
        for name in sync_names:
            sync = getattr(self, name, None)
            if not callable(sync):
                continue
            try:
                sync()
            except Exception as exc:
                errors.append({
                    "name": name,
                    "error": str(exc),
                })

        for list_name in [
            "_buy_exit_time_state_updaters",
        ]:
            for updater in list(getattr(self, list_name, [])):
                if not callable(updater):
                    continue
                try:
                    updater()
                except Exception as exc:
                    errors.append({
                        "name": list_name,
                        "error": str(exc),
                    })
        return errors

    def apply_indicator_follow_ui_state(self, state):
        """Apply a collected UI state in memory only; this never writes rules.json."""
        result = {"applied": [], "skipped": [], "sync_errors": []}
        if not isinstance(state, dict):
            result["skipped"].append({
                "name": "state",
                "reason": "state_not_dict",
            })
            return result

        state = normalize_indicator_follow_basic_ui_state(state)
        self._apply_named_ui_values(state.get("basic", {}), result=result)

        buy_ui = state.get("buy_ui", {})
        if isinstance(buy_ui, dict):
            signal_filter = buy_ui.get("signal_filter", {})
            flat_signal_filter = {
                key: value
                for key, value in signal_filter.items()
                if key != "buy_composite"
            } if isinstance(signal_filter, dict) else {}
            self._apply_named_ui_values(flat_signal_filter, result=result)
            self._apply_buy_composite_ui_state(
                signal_filter.get("buy_composite") if isinstance(signal_filter, dict) else None,
                result=result,
            )
            self._apply_prefixed_ui_values(buy_ui.get("base", {}), "buy_base_", result=result)
            self._apply_prefixed_ui_values(buy_ui.get("repeat", {}), "buy_base_", result=result)
            self._apply_prefixed_ui_values(
                buy_ui.get("price_compare", {}),
                "buy_price_compare_",
                result=result,
            )
            self._apply_prefixed_ui_values(
                buy_ui.get("situation", {}),
                "buy_situation_response_",
                result=result,
            )
            self._apply_existing_prefixed_ui_values(
                buy_ui.get("additional", {}),
                ("buy_additional_active_", "buy_price_compare_skip_"),
                result=result,
            )
            self._apply_existing_prefixed_ui_values(
                buy_ui.get("cycle", {}),
                ("avg_", "buy_cycle_"),
                result=result,
            )
            self._apply_named_ui_values(buy_ui.get("exit", {}), result=result)
        else:
            result["skipped"].append({
                "name": "buy_ui",
                "reason": "values_not_dict",
            })

        sell_ui = state.get("sell_ui", {})
        if isinstance(sell_ui, dict):
            signal_conditions = sell_ui.get("signal_conditions", {})
            if isinstance(signal_conditions, dict):
                self._apply_prefixed_ui_values(
                    signal_conditions.get("condition_a", {}),
                    "sell_signal_condition_a_",
                    result=result,
                )
                self._apply_prefixed_ui_values(
                    signal_conditions.get("condition_b", {}),
                    "sell_signal_condition_b_",
                    result=result,
                )
                self._apply_prefixed_ui_values(
                    signal_conditions.get("condition_c", {}),
                    "sell_signal_condition_c_",
                    result=result,
                )

            selected_sets = sell_ui.get("selected_sets", {})
            if isinstance(selected_sets, dict):
                selected_set_names = {
                    "a": "sell_method_select_a_check",
                    "b": "sell_method_select_b_check",
                    "c": "sell_method_select_c_check",
                }
                ordered_selected_keys = sorted(
                    selected_sets,
                    key=lambda key: not bool(selected_sets[key]),
                )
                for key in ordered_selected_keys:
                    name = selected_set_names.get(key)
                    if name is None:
                        result["skipped"].append({
                            "name": f"sell_ui.selected_sets.{key}",
                            "reason": "unknown_selected_set",
                        })
                        continue
                    if key not in selected_sets:
                        continue
                    skipped = self._apply_ui_widget_value(name, selected_sets[key])
                    if skipped is None:
                        result["applied"].append(name)
                    else:
                        result["skipped"].append(skipped)

            self._apply_prefixed_ui_values(sell_ui.get("setting_a", {}), "sell_a_", result=result)
            self._apply_prefixed_ui_values(sell_ui.get("setting_b", {}), "sell_b_", result=result)
            self._apply_prefixed_ui_values(sell_ui.get("setting_c", {}), "sell_c_", result=result)
        else:
            result["skipped"].append({
                "name": "sell_ui",
                "reason": "values_not_dict",
            })

        result["sync_errors"].extend(self._sync_indicator_follow_ui_after_apply())
        return result

    def collect_indicator_follow_ui_state(self):
        """Collect editable UI values for preview only; this never writes rules.json."""
        basic_names = [
            "basic_signal_interval_combo",
            "basic_duplicate_signal_combo",
            "basic_error_policy_combo",
            "buy_signal_expr_line",
            "sell_signal_expr_line",
            "sell_method_select_a_check",
            "sell_method_select_b_check",
            "sell_method_select_c_check",
        ]

        buy_base_names = sorted(
            name for name in vars(self)
            if name.startswith("buy_base_")
        )
        buy_base_section_names = [
            name for name in buy_base_names
            if (
                name == "buy_base_detail_mode_combo"
                or name.startswith("buy_base_hoga_")
                or name.startswith("buy_base_order_")
                or name.startswith("buy_base_up_")
                or name.startswith("buy_base_down_")
                or name.startswith("buy_base_time_")
                or name.startswith("buy_base_ratio_")
            )
        ]
        buy_repeat_section_names = [
            name for name in buy_base_names
            if name not in set(buy_base_section_names)
        ]
        buy_price_compare_names = sorted(
            name for name in vars(self)
            if name.startswith("buy_price_compare_")
            and not name.startswith("buy_price_compare_skip_")
        )
        buy_additional_names = sorted(
            name for name in vars(self)
            if name.startswith("buy_additional_active_")
            or name.startswith("buy_price_compare_skip_")
        )
        buy_ui = {
            "signal_filter": {
                **self._collect_prefixed_ui_values(("buy_ocr_",)),
                **self._collect_prefixed_ui_values(("buy_bollinger_",)),
                **self._collect_prefixed_ui_values(("buy_ma_",)),
                **self._collect_prefixed_ui_values(("buy_rsi_",)),
                "buy_composite": self._collect_buy_composite_ui_state(),
            },
            "base": self._collect_named_ui_values_without_prefix(
                buy_base_section_names,
                "buy_base_",
            ),
            "repeat": self._collect_named_ui_values_without_prefix(
                buy_repeat_section_names,
                "buy_base_",
            ),
            "price_compare": self._collect_named_ui_values_without_prefix(
                buy_price_compare_names,
                "buy_price_compare_",
            ),
            "situation": self._collect_prefixed_ui_values_without_prefix(
                "buy_situation_response_",
            ),
            "additional": {
                **self._collect_named_ui_values_without_prefix(
                    [
                        name for name in buy_additional_names
                        if name.startswith("buy_additional_active_")
                    ],
                    "buy_additional_active_",
                ),
                **self._collect_named_ui_values_without_prefix(
                    [
                        name for name in buy_additional_names
                        if name.startswith("buy_price_compare_skip_")
                    ],
                    "buy_price_compare_skip_",
                ),
            },
            "cycle": {
                **self._collect_named_ui_values_without_prefix(
                    [
                        "avg_round_increase_check",
                        "avg_amount_increase_check",
                        "avg_active_buy_check",
                    ],
                    "avg_",
                ),
                **self._collect_prefixed_ui_values(("buy_cycle_",)),
            },
            "exit": self._collect_prefixed_ui_values(("buy_exit_",)),
            "close": {},
            "legacy_summary": {},
        }

        complete_names = [
            "complete_current_state_check",
            "complete_policy_remain_buy_option_check",
            "complete_after_cancel_check",
            "complete_fill_ratio_check",
            "complete_fill_ratio_value_line",
            "complete_fill_ratio_compare_combo",
            "complete_fill_ratio_logic_combo",
            "complete_policy_active_buy_option_check",
            "complete_policy_active_buy_price_basis_combo",
            "complete_policy_active_buy_direction_combo",
            "complete_policy_active_buy_value_line",
        ]

        legacy_sell_summary = {}
        legacy_sell_summary.update(
            self._collect_prefixed_ui_values(
                (
                    "sell_method_",
                    "sell_complete_",
                    "macd_sell_",
                    "profit_sell_",
                ),
                excluded_names={
                    "sell_method_avg_point_basis_combo",
                },
            )
        )
        legacy_sell_summary.update(
            self._collect_named_ui_values(
                [
                    "sell_logic_combo",
                    "sell_enabled_check",
                    "target_profit_line",
                    "profit_basis_line",
                ]
            )
        )
        sell_setting_excluded_names = {
            name
            for name in vars(self)
            if (
                name.endswith("_sync")
                or name.endswith("_exit_condition_checks")
                or name.endswith("_complete_policy_check")
                or name.endswith("_complete_policy_label")
                or name.endswith("_complete_policy_result_check")
                or name.endswith("_complete_result_label")
            )
        }
        sell_ui = {
            "signal_conditions": {
                "condition_a": self._collect_prefixed_ui_values_without_prefix(
                    "sell_signal_condition_a_",
                ),
                "condition_b": self._collect_prefixed_ui_values_without_prefix(
                    "sell_signal_condition_b_",
                ),
                "condition_c": self._collect_prefixed_ui_values_without_prefix(
                    "sell_signal_condition_c_",
                ),
            },
            "selected_sets": {
                "a": bool(getattr(self, "sell_method_select_a_check", None).isChecked())
                if getattr(self, "sell_method_select_a_check", None) is not None
                else False,
                "b": bool(getattr(self, "sell_method_select_b_check", None).isChecked())
                if getattr(self, "sell_method_select_b_check", None) is not None
                else False,
                "c": bool(getattr(self, "sell_method_select_c_check", None).isChecked())
                if getattr(self, "sell_method_select_c_check", None) is not None
                else False,
            },
            "setting_a": self._collect_prefixed_ui_values_without_prefix(
                "sell_a_",
                excluded_names=sell_setting_excluded_names,
            ),
            "setting_b": self._collect_prefixed_ui_values_without_prefix(
                "sell_b_",
                excluded_names=sell_setting_excluded_names,
            ),
            "setting_c": self._collect_prefixed_ui_values_without_prefix(
                "sell_c_",
                excluded_names=sell_setting_excluded_names,
            ),
            "legacy_summary": legacy_sell_summary,
        }

        return {
            "basic": self._collect_named_ui_values(basic_names),
            "buy_ui": buy_ui,
            "sell_ui": sell_ui,
            "complete_ui": self._collect_named_ui_values(complete_names),
        }
def main():
    rules_path = sys.argv[1] if len(sys.argv) >= 2 else None
    app = QApplication(sys.argv)
    dlg = IndicatorFollowRoutineSettingsDialog(rules_path=rules_path)
    dlg.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
