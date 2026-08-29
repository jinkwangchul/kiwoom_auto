# Main Footer Status Message Audit

- Audit date: 2026-08-29
- Scope: current local Working Tree Production path
- UI target: MainWindow bottom-row `mainFooterStatusMessage`
- Message wording changes: none
- Definitive numbered inventory: `reports/MAIN_FOOTER_STATUS_MESSAGE_EXHAUSTIVE_LIST.md`

## Rendering And Forwarding Path

The footer label has one rendering boundary:

1. `MainWindow._create_button_area()` creates `main_status_message_label`.
2. `MainWindow._bind_main_status_message_to_button_row()` connects the hidden
   `QStatusBar.messageChanged` signal to `QLabel.setText`.
3. Existing `QStatusBar.showMessage(message, timeout)` calls remain the canonical writer.
4. A timeout clears the hidden status bar and therefore clears the footer label through the
   same signal.

Messages can enter that boundary through four Production adapters:

| Entry | Forwarding path | File |
|---|---|---|
| Main window | `MainWindow.statusBar().showMessage(...)` | `gui_windows.py`, `gui_main_emergency_ops.py` |
| Main stock context | `MainMonitoringStockOperationAdapter.statusBarMessage()` -> MainWindow status bar | `gui_main_stock_context_menu.py:475` |
| Main operation host | `AutoTradeOperationHost.statusBarMessage()` -> owner MainWindow status bar | `gui_auto_trade_operation_host.py:123` |
| AutoTradeSetting | `AutoTradeSettingWindow.statusBarMessage()` -> `persistent_feature_owner(self)` -> MainWindow status bar | `gui_auto_trade_setting_window.py:13063` |

`gui_main_monitoring_preview.py:260` owns a separate preview-only status bar and its
`UI 배치 확인용 시안 · 실제 데이터 및 주문 기능과 연결되지 않음` message does not reach
the Production MainWindow footer. It is excluded from the inventory below.

## Complete Message Inventory

### Initialization, Login, Account, And Recovery

| Message/template | Condition | Caller/function | File |
|---|---|---|---|
| `준비 완료` | Main UI setup complete | `MainWindow._setup_ui` | `gui_windows.py:4134` |
| `운영 재개 승인이 취소되었습니다.` | Operator rejects Startup Recovery approval | `MainWindow.review_startup_recovery` | `gui_windows.py:7265` |
| `운영 재개 승인 완료: {status}` | Operator approves Startup Recovery; `status` is the recovery status | `MainWindow.review_startup_recovery` | `gui_windows.py:7279` |
| `키움 OpenAPI를 사용할 수 없습니다. 설치 상태와 32비트 실행 환경을 확인하십시오.` | API object missing or unavailable | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7292,7305` |
| `로그인 상태: 연결됨` | Login was already connected or login result reports connected | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7314,7348` |
| `키움 로그인 요청 중 오류가 발생했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | `api.login()` raises | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7327` |
| `로그인 요청됨` | Login request accepted/in progress | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7332-7348` |
| `키움 로그인 요청을 완료하지 못했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | Login request result is neither pending nor connected | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7340-7348` |
| `login succeeded` | OpenAPI `OnEventConnect(0)` establishes a session | `KiwoomApi._on_event_connect` -> `MainWindow.on_kiwoom_login_state_changed` | `kiwoom_api.py:2906-2914`, `gui_windows.py:7350-7428` |
| `user info exchange failed` | OpenAPI login error `-100` | same login-state signal path | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` |
| `server connection failed` | OpenAPI login error `-101` | same login-state signal path | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` |
| `version processing failed` | OpenAPI login error `-102` | same login-state signal path | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` |
| `login failed: {code}` | Other OpenAPI login error; `code` is `OnEventConnect` error code | same login-state signal path | `kiwoom_api.py:2923-2930`, `gui_windows.py:7428` |
| `kiwoom api disconnected` | An established API session is observed disconnected | `KiwoomApi._observe_connected_state` -> login-state signal | `kiwoom_api.py:1037-1089`, `gui_windows.py:7428` |
| `미연결 상태` | Login bootstrap window is closed/rejected without connection | `KiwoomApi._observe_login_bootstrap_desktop` -> login-state signal | `kiwoom_api.py:1748-1770`, `gui_windows.py:7428` |
| `로그인 상태: 연결됨` / `로그인 상태: 실패` | Login-state payload contains no message; fallback selected by `connected` | `MainWindow.on_kiwoom_login_state_changed` | `gui_windows.py:7350-7428` |
| `계좌비밀번호 입력 기능을 사용할 수 없습니다.` | API has no callable account-password dialog | `MainWindow.open_current_account_authentication` | `gui_windows.py:7567` |
| `계좌비밀번호 입력창을 열지 못했습니다.` | Account-password dialog call fails | `MainWindow.open_current_account_authentication` | `gui_windows.py:7571` |
| `서버 연결 및 계좌 복구 확인 후 사용할 수 있습니다.` | Emergency stop/release preflight is not ready | `execute_emergency_stop`, `execute_selected_emergency_stop` | `gui_main_emergency_ops.py:70,765,931` |
| `{action}할 수 없습니다. 로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오.[\n\n원인: {detail}]` | Startup recovery guard blocks an action; external detail is appended when safe to expose | `startup_recovery_operation_block_message`, forwarded by setting/main adapters | `gui_auto_trade_run_control.py:1128-1137`, `gui_auto_trade_setting_window.py:5467`, `gui_main_stock_context_menu.py:546` |
| Production recovery reason text family | Recovery decision blocks a stock/global action; exact variants listed below | `MainWindow.production_recovery_block_user_message` | `gui_windows.py:6947-7045` |

Production recovery reason text family:

- `키움 서버에 로그인되어 있지 않습니다.`
- `로그인 세션 정보를 확인할 수 없습니다. 키움 서버에 다시 로그인하십시오.`
- `운영할 계좌를 선택하십시오.`
- `Recovery 데이터를 읽을 수 없습니다. 복구를 다시 실행한 후 운영을 시작하십시오.`
- `운영 시작에 필요한 Recovery 정보를 확인할 수 없습니다. 로그인과 계좌 선택 상태를 확인한 후 Recovery를 다시 실행하십시오.`
- `운영 시작 전에 Recovery가 완료되지 않았습니다. 로그인과 계좌 선택 후 Recovery를 완료하십시오.`
- `Recovery가 진행 중입니다. 복구가 완료된 후 다시 시도하십시오.`
- `복구가 필요한 종목이 남아 있습니다. 검토관리에서 해당 종목을 처리하십시오.`
- `현재 로그인 또는 계좌와 Recovery 정보가 일치하지 않습니다. Recovery를 다시 실행하십시오.`
- `이전 Recovery 정보는 현재 세션에서 사용할 수 없습니다. Recovery를 다시 실행하십시오.`
- `선택한 종목의 Recovery가 아직 완료되지 않았습니다.`
- `선택한 종목은 복구 검토 대상입니다. 검토관리에서 해당 종목을 처리하십시오.`
- `선택한 종목의 Recovery에 실패했습니다. 검토관리에서 상태를 확인하십시오.`
- `Runtime 데이터를 읽을 수 없어 Recovery에 실패했습니다. 검토관리에서 Runtime 상태를 확인하십시오.`
- `계좌의 보유 또는 미체결 정보를 확인하지 못했습니다. 키움 연결 상태를 확인한 후 Recovery를 다시 실행하십시오.`
- `운영 주기 실행을 시작하지 못했습니다. 로그를 확인한 후 Recovery를 다시 실행하십시오.`
- `Recovery가 완료된 운영 대상 종목이 없습니다. 검토관리에서 종목 상태를 확인하십시오.`
- `계좌 Recovery에 실패했습니다. 로그인과 계좌 상태를 확인한 후 Recovery를 다시 실행하십시오.`

### Operation Start, Stop, State, And Selection

| Message/template | Condition | Caller/function | File |
|---|---|---|---|
| `선택한 루틴 정보를 읽을 수 없습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.` | Routine instance lookup fails | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10603-10613` |
| `선택한 루틴에 등록된 종목이 없습니다.\n자동매매 설정에서 종목을 등록하십시오.` | Selected routine has no registered stocks | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10662-10671` |
| `{instance} {requested_action} 완료 (대상 {count}종목)` | Instance start/stop succeeds and backend provides no user message | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10727-10738` |
| `{instance} {requested_action} 실패: {user_message}` | Instance start/stop fails; backend/adapter message inserted | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10741-10752` |
| `{scope} {command}: {display_name} / 대상 0` | Group/category command has no running targets | `MainWindow.request_routine_definition_operation` | `gui_windows.py:11227` |
| `{scope} {command} 취소: {display_name}` | Group/category command confirmation cancelled | `MainWindow.request_routine_definition_operation` | `gui_windows.py:11261` |
| `{scope} {command}: {display_name} / 성공 {applied} / 차단 {failed}` | Group/category close/liquidation command completes | `MainWindow.request_routine_definition_operation` | `gui_windows.py:11319` |
| `루틴 {command}: {display_name} / 대상 0` | Routine command has no running targets | `MainWindow.request_routine_operation` | `gui_windows.py:11365` |
| `루틴 {command} 취소: {display_name}` | Routine command confirmation cancelled | `MainWindow.request_routine_operation` | `gui_windows.py:11391` |
| `루틴 {command}: {display_name} / 성공 {applied} / 차단 {failed}` | Routine close/liquidation command completes | `MainWindow.request_routine_operation` | `gui_windows.py:11447` |
| `관제창 조기마감: 대상 0` | Visible monitoring set has no close target | `MainWindow.request_visible_monitoring_early_close` | `gui_windows.py:10497` |
| `관제창 조기마감 취소` | Visible monitoring early-close confirmation cancelled | same | `gui_windows.py:10529` |
| `관제창 조기마감: 성공 {applied} / 차단 {failed}` | Visible monitoring early close completes | same | `gui_windows.py:10580` |
| `거래권한을 변경할 종목을 1개 이상 선택하세요.` | Permission command has no selected stocks | `toggle_selected_trade_permission` in setting/main adapter | `gui_auto_trade_setting_window.py:5951`, `gui_main_stock_context_menu.py:436` |
| `거래권한 변경: {changed}개[ / 차단 {blocked}개]` | Real/watch-only permission command completes | same | `gui_auto_trade_setting_window.py:5975-5980`, `gui_main_stock_context_menu.py:455-460` |
| `운영 중에는 더블클릭으로 운영 대상을 변경할 수 없습니다. 우클릭 운영시작을 사용하세요.` | Operation exclusion mutation is blocked for a current-running stock | `auto_trade_set_stock_operation_exclusion`, double-click guard | `gui_auto_trade_status_ops.py:82,215-217`, `gui_auto_trade_setting_window.py:3838-3842` |
| `{code} {name} 운영 제외` / `{code} {name} 운영 제외 해제` | Operation exclusion mutation succeeds | `auto_trade_set_stock_operation_exclusion` | `gui_auto_trade_status_ops.py:239-253` |
| `현재 루틴 전체 종목 선택: {row_count}개` | Select-all command | `select_all_current_routine_stocks` | `gui_auto_trade_selection.py:65-68` |
| `현재 루틴 종목 선택 해제` | Clear selection command | `clear_current_routine_stock_selection` | `gui_auto_trade_selection.py:71-74` |
| `루틴 등록해제 완료: {count}개` | Stock unregister succeeds | `unregister_selected_auto_trade_stocks` | `gui_auto_trade_unregister.py:68-145` |
| `{label} 전환 완료: {count}개` | Manual override flag is toggled | `AutoTradeSettingWindow.toggle_selected_manual_override_flag` | `gui_auto_trade_setting_window.py:5989-6036` |
| `수동운영 기본 리셋 완료: {count}개` | Manual override settings reset | `AutoTradeSettingWindow.reset_selected_manual_override` | `gui_auto_trade_setting_window.py:6041-6079` |
| `오늘 운영이 종료되었습니다.` | Global state is `NORMAL_ENDED` before setting-window start | `AutoTradeSettingWindow.start_selected_auto_trades` | `gui_auto_trade_setting_window.py:12938-12942` |
| `운영시작 대상이 없습니다. 운영 제외를 해제한 뒤 다시 시도하세요.` | No selected/startable or running targets | same | `gui_auto_trade_setting_window.py:12960-12964` |
| `신호평가 전용 전환 대상 없음` | Probe-only start has no selection | `start_signal_probe_only_for_selected_stocks` | `gui_auto_trade_run_control.py:1961-1965` |
| `신호평가 전용 시작: {started}개[ / 실패 {failed}개]` | Probe-only start completes | same | `gui_auto_trade_run_control.py:2001-2008` |
| `신호평가 전용 중지 대상 없음` | Probe-only stop has no selection | `stop_signal_probe_only_for_selected_stocks` | `gui_auto_trade_run_control.py:2015-2019` |
| `신호평가 전용 중지: {stopped}개[ / 실패 {failed}개]` | Probe-only stop completes | same | `gui_auto_trade_run_control.py:2051-2058` |
| `전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오.` | Operation start finishes but global operation-state write fails | `auto_trade_start_selected_auto_trades` | `gui_auto_trade_run_control.py:2927,2933` |
| Operation Start `result.user_message` family | Single start, failed start, or partial multi-start writes canonical result text; exact templates listed below | `_show_start_failure_once`, `_show_operation_start_summary_toast` | `gui_auto_trade_run_control.py:1310-1752,1558-1572,1827-1833,2925` |

Operation Start result templates that can reach the footer:

- `{name_or_code} 운영을 시작했습니다.`
- `{stock}은/는 긴급정지 상태입니다.`
- `{stock}은/는 검토관리 대상입니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.`
- `{stock}은/는 이미 운영 중입니다.`
- `{stock}의 필수 운영 설정이 완료되지 않았습니다.\n자동매매 설정을 확인한 뒤 다시 시도하십시오.`
- `{stock}의 현재 세션 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.`
- `{stock}의 초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.`
- `{stock}의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.`
- `{stock}의 운영 상태를 저장하거나 다시 확인하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.`
- `{stock}의 Recovery가 아직 완료되지 않았습니다.\n복구가 완료된 뒤 다시 시도하십시오.`
- `{stock}의 Recovery에 실패했습니다.\n검토관리에서 상태를 확인하십시오.`
- `오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오.`
- `{stock}은/는 시간운영 종료로 운영을 시작할 수 없습니다.`
- `{stock}은/는 현재 운영시작 가능 시간이 아닙니다.`
- `{stock}은/는 보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.`
- `{stock}은/는 미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.`
- `{stock}은/는 취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.`
- `{stock}은/는 마감 또는 청산 절차가 진행 중입니다.`
- `{stock}의 운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.`
- `{stock}은/는 현재 운영을 시작할 수 없는 상태입니다.\n자동매매 설정과 검토관리 상태를 확인하십시오.`
- `모든 종목이 긴급정지 상태입니다.`
- `모든 등록 종목이 검토 대상으로 분리되어 있습니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.`
- `선택한 루틴은 이미 운영 중입니다.`
- `현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.`
- `초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.`
- `모든 등록 종목의 필수 설정이 완료되지 않았습니다.\n자동매매 설정을 확인하십시오.`
- `오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오.`
- `현재는 매매 운영 시간이 아닙니다.`
- `보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.`
- `미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.`
- `취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.`
- `마감 또는 청산 절차가 진행 중이어서 운영을 다시 시작할 수 없습니다.`
- `종목의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.`
- `종목의 운영 상태를 저장하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.`
- `운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.`
- `현재 운영을 시작할 수 있는 종목이 없습니다.\n검토관리와 자동매매 설정을 확인하십시오.`
- Grouped partial result lines: `운영중 유지: {count}종목`, `{reason_label}: {count}종목`, and `- {stock_label}`. Reason labels include `수동운영 최종 세션 종료`, `시간운영 종료`, `검토관리 필요`, `복구 준비 미완료`, `복구 실패`, `복구 검토 필요`, `긴급정지`, `마감/청산 진행`, and `이미 운영중`.
- `운영 시작 대상을 확인하는 중 오류가 발생했습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.`
- `운영 시작 전 서버와 계좌 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.`

### Emergency, ATS, Close, And Liquidation

| Message/template | Condition | Caller/function | File |
|---|---|---|---|
| `전역 긴급정지 기록에 실패했습니다. 종목별 긴급정지를 시작하지 않았습니다.` | Global emergency-stop write fails | `execute_emergency_stop` | `gui_main_emergency_ops.py:791-793` |
| `긴급정지 실행 완료: {changed}개 종목[ / 기존 검토 유지 {count}개][ / 실패 {failed}개 / 전역 차단 유지]` | Global emergency stop completes | `execute_emergency_stop` | `gui_main_emergency_ops.py:878-885` |
| `종목 검토정지: 변경 {changed}개[ / 이미 검토관리 {skipped}개][ / 실패 {failed}개]` | Selected stocks move to review stop | `execute_selected_emergency_stop` | `gui_main_emergency_ops.py:1004-1011` |
| `전체 긴급정지 상태에서는 상단 정지해제를 사용하십시오.` | Per-stock release attempted under global emergency | `execute_selected_emergency_release` | `gui_main_emergency_ops.py:1119-1123` |
| `종목 정지해제: 정상 {normal}개 / 검토관리 유지 {review}개[ / 긴급정지 유지 {blocked}개][ / 대상아님 {skipped}개][ / 실패 {failed}개]` | Selected release completes | same | `gui_main_emergency_ops.py:1179-1187` |
| `전체 정지해제 차단 \| {operator_reason}` | Global release preflight blocks | `release_emergency_stop` | `gui_main_emergency_ops.py:1277-1279` |
| `정지해제 완료: 정상 {normal}개 / 검토관리 {review}개` | Global release completes cleanly | same | `gui_main_emergency_ops.py:1363-1374` |
| `정지해제 미완료: 정상 {normal}개 / 검토관리 {review}개 / 긴급정지 잔존 {remaining}개 / 실패 {failed}개 / 전역 차단 유지` | Global release has remaining/failed targets | same | `gui_main_emergency_ops.py:1363-1374` |
| `ATS 주문방식 설정 오류: INVALID_ATS_EXECUTION_METHOD / {codes}` | Existing ATS method state is invalid; `codes` are affected stocks | `auto_trade_selected_manual_ats_execution_method_state` | `gui_auto_trade_ats_ops.py:91-116` |
| `ATS설정 변경 완료: {label} {ON_or_OFF} / {changed}개` | Manual ATS flag is saved | `auto_trade_set_selected_manual_ats_flag` | `gui_auto_trade_ats_ops.py:349-358` |
| `ATS 주문방식 저장 실패: INVALID_ATS_EXECUTION_METHOD` | Requested ATS method is invalid | `auto_trade_set_selected_manual_ats_execution_method` | `gui_auto_trade_ats_ops.py:361-376` |
| `ATS 주문방식 변경 완료: {method_label} / {succeeded}개` | ATS execution method save completes | same | `gui_auto_trade_ats_ops.py:386-388` |
| `ATS {method}매도 취소` | Operator cancels ATS liquidation confirmation | `auto_trade_execute_selected_manual_ats_liquidation` | `gui_auto_trade_ats_ops.py:865-992` |
| `ATS {method}매도 SendOrder 접수 기록: {count}개` | ATS sell submission records complete | same | `gui_auto_trade_ats_ops.py:1040-1042` |
| `ATS {method}매도 취소 확인 대기: {count}개` | ATS liquidation waits for cancel confirmation | same | `gui_auto_trade_ats_ops.py:1044-1046` |
| `ATS {method}매도 청산 대상 없음: {count}개` | ATS liquidation finds no holding | same | `gui_auto_trade_ats_ops.py:1048-1050` |
| `개별청산 설정 완료: {minutes}분/{method} / 대상 {count}개` | Individual liquidation settings saved | `auto_trade_apply_selected_individual_liquidation_method` | `gui_auto_trade_close.py:858-1004` |
| `현재 상태는 마감정책 취소 대상이 아닙니다.` | Main context early-close cancellation is not safe/applicable | `auto_trade_cancel_selected_early_close` | `gui_auto_trade_close.py:1353-1425` |
| `마감정책이 취소되었습니다.` | Main context early-close cancellation succeeds | same | `gui_auto_trade_close.py:1353-1425` |
| `조기마감 불가: 청산 진행 중` | Selected target already has active liquidation | `auto_trade_apply_selected_early_close` | `gui_auto_trade_close.py:1525` |
| `조기마감 적용: 0개` | No eligible early-close targets | same | `gui_auto_trade_close.py:1530` |
| `조기마감 취소` | Operator cancels early-close confirmation | same | `gui_auto_trade_close.py:1573` |
| `조기마감 적용: {completed}개[ / 제외 {skipped}개]` | Early-close processing completes | same | `gui_auto_trade_close.py:1753-1757` |

### Timers, Data, Configuration, And Errors

| Message/template | Condition | Caller/function | File |
|---|---|---|---|
| `Runtime 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | Runtime refresh timer raises | `AutoTradeSettingWindow.on_runtime_file_timer_tick` | `gui_auto_trade_setting_window.py:5404-5416` |
| `시간정책 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | Time-policy refresh timer raises | `AutoTradeSettingWindow.on_time_policy_timer_tick` | `gui_auto_trade_setting_window.py:5418-5428` |
| `자동매매 운영 주기를 처리하지 못했습니다. 로그를 확인하십시오.` | Main operation host cycle raises | `AutoTradeOperationHost._on_operation_cycle_timeout` | `gui_auto_trade_operation_host.py:548-550` |
| `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors} / 후보 {created} / 승인검사 {approval_checked} / 승인 {approved}` | Timer signal pipeline produces work/errors | `_process_pending_signal_pipeline` | `gui_auto_trade_timer.py:148-204` |
| `실자동매매 주문처리: 실행 {processed} / 차단 {blocked}` | Automated real-order processing has activity | same | `gui_auto_trade_timer.py:238-240` |
| `루틴 신호 로그: 기록 {logged}개[ / 오류 {errors}개]` | Signal probe writes records/errors | `_auto_trade_run_signal_cycle` | `gui_auto_trade_timer.py:244-276` |
| `주문 후보를 검증하는 중 오류가 발생했습니다. 로그를 확인하십시오.` | Signal cycle raises | same | `gui_auto_trade_timer.py:305-309` |
| `마감·청산 Command 처리: 진행 {processed} / 차단 {blocked}` | Close command timer has activity | `auto_trade_run_operation_cycle` | `gui_auto_trade_timer.py:487-490` |
| `ATS 청산 Command 처리: 진행 {processed} / 실패 {failed}` | ATS liquidation timer has activity | same | `gui_auto_trade_timer.py:496-499` |
| `시간정책 자동반영: 변경 {changed}개[ / 실패 {failed}개]` | Time-policy projection mutates statuses | `auto_trade_on_time_policy_timer_tick` | `gui_auto_trade_timer.py:666-669` |
| `환경설정 저장 완료` | Operation environment dialog saves | `AutoTradeSettingWindow._handle_operation_environment_settings_saved` | `gui_auto_trade_setting_window.py:12743-12755` |
| `분봉조회할 종목 1개를 선택하세요.` | Minute-candle request has invalid selection count | `AutoTradeSettingWindow.fetch_minute_candles_for_selected_stock` | `gui_auto_trade_setting_window.py:10402` |
| `키움 API가 초기화되지 않았습니다.` | Minute-candle request has no API | same | `gui_auto_trade_setting_window.py:10409` |
| `키움 API 사용불가: {reason}` | API exists but is unavailable | same | `gui_auto_trade_setting_window.py:10414` |
| `키움 로그인 후 분봉조회가 가능합니다.` | API is disconnected | same | `gui_auto_trade_setting_window.py:10418` |
| `{code} {name} candles.json 저장 완료: {saved_count}개[ ({warning_or_more_pages})]` | Async minute-candle callback succeeds | `handle_result` inside same function | `gui_auto_trade_setting_window.py:10422-10428` |
| `{code} {name} 분봉조회 요청됨` | Minute-candle request accepted | same | `gui_auto_trade_setting_window.py:10447` |
| `{code} {name} 분봉조회 실패: {message_or_exception}` | Callback/request failure | same | `gui_auto_trade_setting_window.py:10432,10443,10450` |
| `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}` | Manual dry-run preview completes | `AutoTradeSettingWindow.preview_order_candidates_for_pending_signals` | `gui_auto_trade_setting_window.py:10452-10464` |
| `주문후보검증 실패: {exception}` | Manual dry-run preview raises | same | `gui_auto_trade_setting_window.py:10466` |

### Manual Execution Diagnostic Messages

These messages are reachable through `AutoTradeSettingWindow.statusBarMessage()` and therefore
the MainWindow footer when that window's persistent owner is MainWindow.

| Message/template | Condition | Caller/function | File |
|---|---|---|---|
| `수동 실주문 후보 활성화: order_id를 입력하세요.` | Missing candidate order id | `enable_execution_candidate_manually` | `gui_auto_trade_setting_window.py:10634` |
| `수동 실주문 후보 활성화 차단` | Any candidate activation guard blocks | same | `gui_auto_trade_setting_window.py:10651,10674,10694` |
| `수동 실주문 후보 활성화 취소` | Operator cancels activation | same | `gui_auto_trade_setting_window.py:10678` |
| `수동 실주문 후보 활성화 {status_text}` | Candidate activation returns status | same | `gui_auto_trade_setting_window.py:10705` |
| `REAL_READY 수동 점검: order_id를 입력하세요.` | Missing preflight order id | `run_real_ready_preflight_manually` | `gui_auto_trade_setting_window.py:10919` |
| `REAL_READY 수동 점검 차단` | Korean preflight guard blocks | same | `gui_auto_trade_setting_window.py:10937,10956,11001,11018` |
| `REAL_READY manual preflight cancelled` | Operator cancels preflight | same | `gui_auto_trade_setting_window.py:10961` |
| `REAL_READY manual preflight blocked` | Runtime preflight result blocks | same | `gui_auto_trade_setting_window.py:10978` |
| `REAL_READY 수동 점검 {status_text}` | Preflight completes | same | `gui_auto_trade_setting_window.py:11030` |
| `Execution Preview: order_id를 입력하세요.` | Missing preview order id | `preview_execution_for_real_ready_order_manual` | `gui_auto_trade_setting_window.py:11229` |
| `Execution Preview blocked: real trade guard is not ready` | Real-trade guard is not ready | same | `gui_auto_trade_setting_window.py:11239` |
| `Execution Preview cancelled before runtime commit confirmation` | Operator cancels before runtime commit | same | `gui_auto_trade_setting_window.py:11253` |
| `Execution Preview {status_text}: {order_id}` | Preview completes | same | `gui_auto_trade_setting_window.py:11315` |
| `Execution Preview 실패: {exception}` | Preview raises | same | `gui_auto_trade_setting_window.py:11317` |
| `수동 Queue 저장: 먼저 유효한 Execution Preview를 실행하세요.` | No valid preview exists | `commit_last_execution_preview_queue_manually` | `gui_auto_trade_setting_window.py:11563` |
| `수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.` | Preview/queue snapshot is stale or invalid | same | `gui_auto_trade_setting_window.py:11582,11597` |
| `Manual Queue commit blocked: runtime commit result is required` | Runtime commit evidence missing | same | `gui_auto_trade_setting_window.py:11613` |
| `수동 Queue 저장: 취소됨` | Operator cancels queue commit | same | `gui_auto_trade_setting_window.py:11617` |
| `Manual Queue commit blocked: readiness policy failed` | Queue readiness policy blocks | same | `gui_auto_trade_setting_window.py:11642` |
| `수동 Queue 저장 {status_text}` | Queue commit completes | same | `gui_auto_trade_setting_window.py:11676` |
| `Manual Cancel: source order id is required` | Manual cancel source id missing | `cancel_pending_order_manually` | `gui_auto_trade_setting_window.py:12049` |
| `Manual Cancel cancelled` | Operator cancels manual cancel | same | `gui_auto_trade_setting_window.py:12093` |
| `Manual Modify: source order id is required` | Manual modify source id missing | `modify_pending_order_manually` | `gui_auto_trade_setting_window.py:12133` |
| `Manual Modify cancelled` | Operator cancels manual modify | same | `gui_auto_trade_setting_window.py:12203` |
| `Manual SendOrder: ORDER_QUEUED record id is required` | Queued record id missing | `send_order_for_order_queued_manually` | `gui_auto_trade_setting_window.py:12246` |
| `Manual SendOrder blocked` | Any read/status/environment/snapshot/final-gate/dispatch guard blocks | same | `gui_auto_trade_setting_window.py:12264-12488` |
| `Manual SendOrder cancelled` | Operator cancels before send | same | `gui_auto_trade_setting_window.py:12320` |
| `Manual SendOrder {status_text}` | Manual send boundary returns final status | same | `gui_auto_trade_setting_window.py:12512` |

## Classification And Findings

### Normal Operating Status

- `준비 완료`
- selection, exclusion, permission, manual override, ATS, close/liquidation, and settings-save
  completion messages.
- count-bearing timer messages when work was actually processed.

### Login And Server Authentication

- Login request/connected/unavailable messages from `MainWindow.login_kiwoom_manually`.
- Raw OpenAPI login event messages from `KiwoomApi._on_event_connect`.
- Account authentication dialog availability errors.
- Recovery and account readiness blockers.

### Operation Start And Stop

- Instance/group/routine start/stop and close result templates.
- Canonical single/multi-target Operation Start `result.user_message` templates.
- Emergency stop/release and probe-only start/stop templates.

### Errors And Warnings

- Runtime/time-policy/operation-cycle errors.
- Operation-state persistence failure.
- Recovery, ATS validation, minute-candle, and order-candidate failures.
- Manual execution guard failures.

### English Or Development-Oriented Production Messages

The following are confirmed reachable in Production and were not changed by this task:

- `login succeeded`
- `user info exchange failed`
- `server connection failed`
- `version processing failed`
- `login failed: {code}`
- `kiwoom api disconnected`
- `ATS 주문방식 설정 오류: INVALID_ATS_EXECUTION_METHOD / {codes}`
- `ATS 주문방식 저장 실패: INVALID_ATS_EXECUTION_METHOD`
- `REAL_READY manual preflight cancelled`
- `REAL_READY manual preflight blocked`
- all `Execution Preview ...` English templates
- `Manual Queue commit blocked: ...`
- all `Manual Cancel ...`, `Manual Modify ...`, and `Manual SendOrder ...` templates

### Duplicate Or Overlapping Meaning

- Connected state has both `로그인 상태: 연결됨` and raw `login succeeded`.
- Login failure has a Korean manual-request family and raw English OpenAPI error family.
- Recovery blockers are emitted by both `startup_recovery_operation_block_message()` and
  `MainWindow.production_recovery_block_user_message()` with overlapping user meaning.
- `주문후보검증` has a timer form with candidate/approval counts and a manual preview form
  without those counts.
- `Manual SendOrder blocked` is intentionally reused by many distinct guard stages, so the
  footer text alone cannot identify the first blocking stage.
- Multi-line recovery/start messages are forwarded into a fixed-height one-row footer; the
  source text is preserved, but the footer is not a detailed diagnostic surface.

No message was deleted, translated, shortened, or otherwise modified during this audit.
