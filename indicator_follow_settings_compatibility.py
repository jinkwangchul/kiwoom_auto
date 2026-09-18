"""Pure compatibility rules for indicator-follow persisted UI state."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_SELL_SELECTED_SET_WIDGET_FIELDS = {
    "a": "sell_method_select_a_check",
    "b": "sell_method_select_b_check",
    "c": "sell_method_select_c_check",
}


_INDICATOR_FOLLOW_CANONICAL_FRESH_DEFAULTS: dict[str, Any] = {'basic': {'basic_signal_interval_combo': '3',
           'basic_duplicate_signal_combo': '선행신호 우선',
           'basic_error_policy_combo': '매매중지',
           'buy_signal_expr_line': 'A',
           'sell_signal_expr_line': 'A'},
 'buy_ui': {'signal_filter': {'buy_ocr_bar_line': '0',
                              'buy_ocr_compare_combo': '이하',
                              'buy_ocr_sign_combo': '-',
                              'buy_ocr_turn_combo': '상승',
                              'buy_ocr_value_line': '0',
                              'buy_bollinger_compare_combo': '이하',
                              'buy_bollinger_direction_combo': '하향',
                              'buy_bollinger_sign_combo': '+',
                              'buy_bollinger_value_line': '0.5',
                              'buy_ma_compare_combo': '돌파',
                              'buy_ma_direction_combo': '상향',
                              'buy_ma_value_line': '60',
                              'buy_rsi_compare_combo': '이하',
                              'buy_rsi_period_line': '14',
                              'buy_rsi_value_line': '30',
                              'buy_composite': {'enabled': False,
                                                'logic': 'OR',
                                                'include_unreferenced_active_filters': 'AND_REQUIRED',
                                                'groups': [{'enabled': False,
                                                            'logic': 'AND',
                                                            'filters': []},
                                                           {'enabled': False,
                                                            'logic': 'AND',
                                                            'filters': []}]}},
            'base': {'down_line': '2',
                     'hoga_combo': '단일호가',
                     'order_combo': '주문가',
                     'ratio_compare_combo': '이상',
                     'ratio_count_line': '3',
                     'ratio_direction_combo': '상향',
                     'ratio_left_combo': '주문가',
                     'ratio_right_combo': '주문가',
                     'ratio_value_line': '0.15',
                     'time_count_line': '3',
                     'time_mode_combo': '선택없음',
                     'time_order_combo': '현재가',
                     'time_range_combo': '이내',
                     'time_unit_combo': '초',
                     'time_value_line': '10',
                     'up_line': '0',
                     'last_round_active_buy': {'enabled': False,
                                               'checked': True,
                                               'direction_combo': '상향',
                                               'ratio_line': '0.2',
                                               'compare_combo': '이하'}},
            'repeat': {'active_compare_combo': '이상',
                       'active_direction_combo': '상향',
                       'active_ratio_line': '0.45',
                       'apply_all_check': True,
                       'budget_ratio_line': '0.5',
                       'detail_mode_combo': '회차기준',
                       'round_budget_line': '2',
                       'round_operator_combo': '+'},
            'price_compare': {'above_active_compare_combo': '이상',
                              'above_active_direction_combo': '상향',
                              'above_active_ratio_line': '0.45',
                              'above_budget_ratio_line': '0.5',
                              'above_condition_combo': '>',
                              'above_mode_combo': '회차기준',
                              'above_round_budget_line': '2',
                              'above_round_operator_combo': '+',
                              'budget_ratio_line': '0.5',
                              'check': False,
                              'condition_combo': '<=',
                              'mode_combo': '회차기준',
                              'round_budget_line': '1',
                              'round_operator_combo': 'x'},
            'situation': {'price_enabled_check': False,
                          'setting1_action_combo': '일괄취소',
                          'setting1_compare_combo': '이상',
                          'setting1_direction_combo': '상향',
                          'setting1_enabled_check': True,
                          'setting1_left_combo': '평단가',
                          'setting1_ratio_line': '0.15',
                          'setting1_right_combo': '현재가',
                          'setting2_action_combo': '매수리셋',
                          'setting2_compare_combo': '이하',
                          'setting2_direction_combo': '하향',
                          'setting2_enabled_check': True,
                          'setting2_left_combo': '평단가',
                          'setting2_ratio_line': '0.15',
                          'setting2_right_combo': '현재가',
                          'unfilled_enabled_check': True,
                          'unfilled_scope_combo': '일괄',
                          'unfilled_time_line': '5',
                          'unfilled_unit_combo': '초'},
            'additional': {'last_plus_one': {'check': False,
                                             'compare_combo': '이하',
                                             'direction_combo': '하향',
                                             'method_combo': '능동',
                                             'ratio_line': '0.2'},
                           'price_compare_skip': {'check': False,
                                                  'compare_combo': '이상',
                                                  'direction_combo': '상향',
                                                  'ratio_line': '0.1',
                                                  'action': 'SKIP_CURRENT_GENERATION'}},
            'cycle': {'buy_cycle_hoga_down_line': '2',
                      'buy_cycle_hoga_mode_combo': '단일호가',
                      'buy_cycle_hoga_up_line': '0',
                      'buy_cycle_order_combo': '주문가',
                      'buy_cycle_ratio_compare_combo': '이상',
                      'buy_cycle_ratio_count_line': '3',
                      'buy_cycle_ratio_direction_combo': '상향',
                      'buy_cycle_ratio_left_combo': '주문가',
                      'buy_cycle_ratio_right_combo': '현재가',
                      'buy_cycle_ratio_value_line': '0.15',
                      'buy_cycle_time_count_line': '3',
                      'buy_cycle_time_mode_combo': '선택없음',
                      'buy_cycle_time_order_combo': '현재가',
                      'buy_cycle_time_range_combo': '이내',
                      'buy_cycle_time_unit_combo': '초',
                      'buy_cycle_time_value_line': '10'},
            'exit': {'buy_exit_count_check': True,
                     'buy_exit_count_line': '3',
                     'buy_exit_price_check': True,
                     'buy_exit_price_compare_combo': '이내',
                     'buy_exit_price_direction_combo': '상하',
                     'buy_exit_price_left_combo': '현재가',
                     'buy_exit_price_right_combo': '평단가',
                     'buy_exit_price_value_line': '0.45',
                     'buy_exit_time_check': True,
                     'buy_exit_time_line': '2',
                     'buy_exit_time_unit_combo': '분'},
            'close': {},
            'legacy_summary': {}},
 'sell_ui': {'signal_conditions': {'condition_a': {'gap_check': True,
                                                   'gap_compare_combo': '이상',
                                                   'gap_direction_combo': '상향',
                                                   'gap_left_combo': '평단가',
                                                   'gap_logic_combo': 'AND',
                                                   'gap_right_combo': '현재가',
                                                   'gap_value_line': '0.5',
                                                   'ocr_check': False,
                                                   'ocr_compare_combo': '이상',
                                                   'ocr_convert_line': '0',
                                                   'ocr_direction_combo': '하락',
                                                   'ocr_logic_combo': 'AND',
                                                   'ocr_sign_combo': '+',
                                                   'ocr_value_line': '0',
                                                   'rsi_check': False,
                                                   'rsi_compare_combo': '이상',
                                                   'rsi_period_line': '14',
                                                   'rsi_value_line': '70'},
                                   'condition_b': {'bollinger_check': False,
                                                   'bollinger_compare_combo': '이상',
                                                   'bollinger_direction_combo': '상향',
                                                   'bollinger_logic_combo': 'AND',
                                                   'bollinger_sign_combo': '-',
                                                   'bollinger_value_line': '0.2',
                                                   'gap_check': False,
                                                   'gap_compare_combo': '이상',
                                                   'gap_direction_combo': '상향',
                                                   'gap_left_combo': '평단가',
                                                   'gap_right_combo': '현재가',
                                                   'gap_value_line': '0.5',
                                                   'price_box_check': False,
                                                   'price_box_compare_combo': '이상',
                                                   'price_box_direction_combo': '상향',
                                                   'price_box_logic_combo': 'AND',
                                                   'price_box_sign_combo': '-',
                                                   'price_box_value_line': '1'},
                                   'condition_c': {'array_check': False,
                                                   'array_first_compare_combo': '>',
                                                   'array_first_period_combo': '5',
                                                   'array_second_compare_combo': '>',
                                                   'array_second_period_combo': '20',
                                                   'array_third_period_combo': '60',
                                                   'gap_check': False,
                                                   'gap_compare_combo': '이내',
                                                   'gap_direction_combo': '상하',
                                                   'gap_left_combo': '평단가',
                                                   'gap_logic_combo': 'AND',
                                                   'gap_right_combo': '현재가',
                                                   'gap_value_line': '0.25',
                                                   'macd_check': False,
                                                   'macd_compare_combo': '이상',
                                                   'macd_kind_combo': 'MACD선',
                                                   'macd_logic_combo': 'AND',
                                                   'macd_sign_combo': '+',
                                                   'macd_value_line': '0'}},
             'selected_sets': {'a': True, 'b': False, 'c': False},
             'setting_a': {'complete_policy_carry_check': True,
                           'complete_policy_market_check': True,
                           'exit_count_check': True,
                           'exit_count_line': '3',
                           'exit_price_check': True,
                           'exit_price_compare': '이상',
                           'exit_price_direction': '상향',
                           'exit_price_left': '평단가',
                           'exit_price_right': '현재가',
                           'exit_price_value': '0.5',
                           'exit_time_check': False,
                           'exit_time_line': '2',
                           'exit_time_unit': '분',
                           'perform1_multi_down_line': '0',
                           'perform1_multi_up_line': '2',
                           'perform1_single_combo': '주문가',
                           'perform1_title_combo': '단일호가',
                           'perform2_ratio_compare': '이상',
                           'perform2_ratio_count': '3',
                           'perform2_ratio_direction': '상향',
                           'perform2_ratio_left': '주문가',
                           'perform2_ratio_right': '주문가',
                           'perform2_ratio_value': '0.15',
                           'perform2_time_count': '3',
                           'perform2_time_order': '주문가',
                           'perform2_time_range': '이내',
                           'perform2_time_unit': '초',
                           'perform2_time_value': '30',
                           'perform2_title_combo': '선택없음',
                           'perform3_pending_scope': '일괄',
                           'perform3_pending_unit': '초',
                           'perform3_pending_value': '5',
                           'perform3_price_action': '일괄취소',
                           'perform3_price_compare': '이하',
                           'perform3_price_direction': '하향',
                           'perform3_price_left': '평단가',
                           'perform3_price_right': '현재가',
                           'perform3_price_value': '0.15',
                           'perform3_title_combo': '미체결',
                           'repeat_perform1_multi_down_line': '0',
                           'repeat_perform1_multi_up_line': '3',
                           'repeat_perform1_single_combo': '주문가',
                           'repeat_perform1_title_combo': '단일호가',
                           'repeat_perform2_ratio_compare': '이상',
                           'repeat_perform2_ratio_count': '3',
                           'repeat_perform2_ratio_direction': '상향',
                           'repeat_perform2_ratio_left': '주문가',
                           'repeat_perform2_ratio_right': '주문가',
                           'repeat_perform2_ratio_value': '0.15',
                           'repeat_perform2_time_count': '3',
                           'repeat_perform2_time_order': '현재가',
                           'repeat_perform2_time_range': '이내',
                           'repeat_perform2_time_unit': '초',
                           'repeat_perform2_time_value': '30',
                           'repeat_perform2_title_combo': '선택없음',
                           'repeat_perform3_pending_scope': '일괄',
                           'repeat_perform3_pending_unit': '초',
                           'repeat_perform3_pending_value': '5',
                           'repeat_perform3_price_action': '매도리셋',
                           'repeat_perform3_price_compare': '이상',
                           'repeat_perform3_price_direction': '상향',
                           'repeat_perform3_price_left': '평단가',
                           'repeat_perform3_price_right': '현재가',
                           'repeat_perform3_price_value': '0.25',
                           'repeat_perform3_title_combo': '미체결'},
             'setting_b': {'complete_policy_carry_check': True,
                           'complete_policy_market_check': True,
                           'exit_count_check': True,
                           'exit_count_line': '3',
                           'exit_price_check': True,
                           'exit_price_compare': '이상',
                           'exit_price_direction': '상향',
                           'exit_price_left': '평단가',
                           'exit_price_right': '현재가',
                           'exit_price_value': '0.5',
                           'exit_time_check': False,
                           'exit_time_line': '2',
                           'exit_time_unit': '분',
                           'perform1_multi_down_line': '0',
                           'perform1_multi_up_line': '2',
                           'perform1_single_combo': '주문가',
                           'perform1_title_combo': '단일호가',
                           'perform2_ratio_compare': '이상',
                           'perform2_ratio_count': '3',
                           'perform2_ratio_direction': '상향',
                           'perform2_ratio_left': '주문가',
                           'perform2_ratio_right': '주문가',
                           'perform2_ratio_value': '0.15',
                           'perform2_time_count': '3',
                           'perform2_time_order': '주문가',
                           'perform2_time_range': '이내',
                           'perform2_time_unit': '초',
                           'perform2_time_value': '30',
                           'perform2_title_combo': '선택없음',
                           'perform3_pending_scope': '일괄',
                           'perform3_pending_unit': '초',
                           'perform3_pending_value': '5',
                           'perform3_price_action': '일괄취소',
                           'perform3_price_compare': '이하',
                           'perform3_price_direction': '하향',
                           'perform3_price_left': '평단가',
                           'perform3_price_right': '현재가',
                           'perform3_price_value': '0.15',
                           'perform3_title_combo': '미체결',
                           'repeat_perform1_multi_down_line': '0',
                           'repeat_perform1_multi_up_line': '3',
                           'repeat_perform1_single_combo': '주문가',
                           'repeat_perform1_title_combo': '단일호가',
                           'repeat_perform2_ratio_compare': '이상',
                           'repeat_perform2_ratio_count': '3',
                           'repeat_perform2_ratio_direction': '상향',
                           'repeat_perform2_ratio_left': '주문가',
                           'repeat_perform2_ratio_right': '주문가',
                           'repeat_perform2_ratio_value': '0.15',
                           'repeat_perform2_time_count': '3',
                           'repeat_perform2_time_order': '현재가',
                           'repeat_perform2_time_range': '이내',
                           'repeat_perform2_time_unit': '초',
                           'repeat_perform2_time_value': '30',
                           'repeat_perform2_title_combo': '선택없음',
                           'repeat_perform3_pending_scope': '일괄',
                           'repeat_perform3_pending_unit': '초',
                           'repeat_perform3_pending_value': '5',
                           'repeat_perform3_price_action': '매도리셋',
                           'repeat_perform3_price_compare': '이상',
                           'repeat_perform3_price_direction': '상향',
                           'repeat_perform3_price_left': '평단가',
                           'repeat_perform3_price_right': '현재가',
                           'repeat_perform3_price_value': '0.25',
                           'repeat_perform3_title_combo': '미체결'},
             'setting_c': {'complete_policy_carry_check': True,
                           'complete_policy_market_check': True,
                           'exit_count_check': True,
                           'exit_count_line': '3',
                           'exit_price_check': True,
                           'exit_price_compare': '이상',
                           'exit_price_direction': '상향',
                           'exit_price_left': '평단가',
                           'exit_price_right': '현재가',
                           'exit_price_value': '0.5',
                           'exit_time_check': False,
                           'exit_time_line': '2',
                           'exit_time_unit': '분',
                           'perform1_multi_down_line': '0',
                           'perform1_multi_up_line': '2',
                           'perform1_single_combo': '주문가',
                           'perform1_title_combo': '단일호가',
                           'perform2_ratio_compare': '이상',
                           'perform2_ratio_count': '3',
                           'perform2_ratio_direction': '상향',
                           'perform2_ratio_left': '주문가',
                           'perform2_ratio_right': '주문가',
                           'perform2_ratio_value': '0.15',
                           'perform2_time_count': '3',
                           'perform2_time_order': '주문가',
                           'perform2_time_range': '이내',
                           'perform2_time_unit': '초',
                           'perform2_time_value': '30',
                           'perform2_title_combo': '선택없음',
                           'perform3_pending_scope': '일괄',
                           'perform3_pending_unit': '초',
                           'perform3_pending_value': '5',
                           'perform3_price_action': '일괄취소',
                           'perform3_price_compare': '이하',
                           'perform3_price_direction': '하향',
                           'perform3_price_left': '평단가',
                           'perform3_price_right': '현재가',
                           'perform3_price_value': '0.15',
                           'perform3_title_combo': '미체결',
                           'repeat_perform1_multi_down_line': '0',
                           'repeat_perform1_multi_up_line': '3',
                           'repeat_perform1_single_combo': '주문가',
                           'repeat_perform1_title_combo': '단일호가',
                           'repeat_perform2_ratio_compare': '이상',
                           'repeat_perform2_ratio_count': '3',
                           'repeat_perform2_ratio_direction': '상향',
                           'repeat_perform2_ratio_left': '주문가',
                           'repeat_perform2_ratio_right': '주문가',
                           'repeat_perform2_ratio_value': '0.15',
                           'repeat_perform2_time_count': '3',
                           'repeat_perform2_time_order': '현재가',
                           'repeat_perform2_time_range': '이내',
                           'repeat_perform2_time_unit': '초',
                           'repeat_perform2_time_value': '30',
                           'repeat_perform2_title_combo': '선택없음',
                           'repeat_perform3_pending_scope': '일괄',
                           'repeat_perform3_pending_unit': '초',
                           'repeat_perform3_pending_value': '5',
                           'repeat_perform3_price_action': '매도리셋',
                           'repeat_perform3_price_compare': '이상',
                           'repeat_perform3_price_direction': '상향',
                           'repeat_perform3_price_left': '평단가',
                           'repeat_perform3_price_right': '현재가',
                           'repeat_perform3_price_value': '0.25',
                           'repeat_perform3_title_combo': '미체결'},
             'legacy_summary': {'sell_method_select_a_check': True,
                                'sell_method_select_b_check': False,
                                'sell_method_select_c_check': False}},
 'complete_ui': {}}


def get_canonical_fresh_defaults(definition_id: str) -> dict[str, Any] | None:
    """Return the user-approved fresh-registration defaults for this routine."""
    if str(definition_id or "").strip() != "indicator_follow":
        return None
    return deepcopy(_INDICATOR_FOLLOW_CANONICAL_FRESH_DEFAULTS)


def normalize_sell_selected_set_authority(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Make ``sell_ui.selected_sets`` authoritative and mirrors derived.

    Older snapshots may have persisted the same checkboxes in ``basic`` or
    ``sell_ui.legacy_summary``.  Those fields are accepted only when the
    canonical selected-set mapping is absent.  An explicit canonical mapping
    always wins, including an invalid mapping that must remain visible to
    validation rather than being silently repaired.
    """
    normalized = deepcopy(state) if isinstance(state, dict) else state
    if not isinstance(normalized, dict):
        return normalized

    basic = normalized.get("basic")
    if not isinstance(basic, dict):
        basic = {}
        normalized["basic"] = basic
    sell_ui = normalized.get("sell_ui")
    if not isinstance(sell_ui, dict):
        sell_ui = {}
        normalized["sell_ui"] = sell_ui
    legacy_summary = sell_ui.get("legacy_summary")
    if not isinstance(legacy_summary, dict):
        legacy_summary = {}

    selected_sets = sell_ui.get("selected_sets")
    if not isinstance(selected_sets, dict):
        source = None
        if any(name in basic for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values()):
            source = basic
        elif any(
            name in legacy_summary
            for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values()
        ):
            source = legacy_summary
        if source is not None:
            selected_sets = {
                key: bool(source.get(name))
                for key, name in _SELL_SELECTED_SET_WIDGET_FIELDS.items()
            }
            sell_ui["selected_sets"] = selected_sets

    for name in _SELL_SELECTED_SET_WIDGET_FIELDS.values():
        basic.pop(name, None)

    if isinstance(selected_sets, dict):
        derived_summary = dict(legacy_summary)
        for key, name in _SELL_SELECTED_SET_WIDGET_FIELDS.items():
            derived_summary[name] = bool(selected_sets.get(key))
        sell_ui["legacy_summary"] = derived_summary
    return normalized


def canonical_indicator_follow_ui_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Return the stable state shape used by exact round-trip comparisons."""
    normalized = normalize_sell_selected_set_authority(state)
    if not isinstance(normalized, dict):
        return normalized
    price_compare = (
        normalized.get("buy_ui", {}).get("price_compare", {})
        if isinstance(normalized.get("buy_ui"), dict)
        else {}
    )
    if (
        isinstance(price_compare, dict)
        and price_compare.get("condition_combo") == "=<"
    ):
        price_compare["condition_combo"] = "<="
    return normalized


def legacy_buy_bollinger_sign(signal_filter: dict[str, Any]) -> str | None:
    """Return the historical implicit BUY Bollinger sign, if it is knowable."""
    direction = str(signal_filter.get("buy_bollinger_direction_combo") or "").strip()
    return {
        "하향": "-",
        "상향": "+",
    }.get(direction)


def legacy_sell_signed_percent_sign(
    condition: dict[str, Any],
    *,
    compare_field: str,
) -> str | None:
    """Return the historical implicit SELL offset sign, if it is knowable."""
    compare = str(condition.get(compare_field) or "").strip().upper()
    return {
        "이상": "+",
        ">=": "+",
        "GTE": "+",
        "이하": "-",
        "<=": "-",
        "LTE": "-",
    }.get(compare)
