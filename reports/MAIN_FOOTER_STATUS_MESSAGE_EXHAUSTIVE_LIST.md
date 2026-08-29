# 메인 하단 운영 메시지 전체 원문 목록

- 기준일: 2026-08-29
- 기준: 현재 로컬 Working Tree
- 조사 성격: 조사/보고 전용
- 문구, UI, Runtime, 주문 코드 변경: 없음

## 집계 기준

- 메인 하단 `QLabel`까지 실제 도달 가능한 고유 원문/Template만 Production 총계에 포함했다.
- 동일 원문이 여러 Caller에서 출력되면 한 종류로 세고 Caller를 모두 기록했다.
- `{name}` 표기는 실제 코드의 동적 값을 정규화한 Template이다.
- `영문`은 원문에 한글이 없는 문자열이다. 한글과 내부 영문 토큰이 섞인 문자열은 `한글/혼합`으로 셌다.
- Toast, MessageBox, logger, 이벤트 저널 문구는 제외했다.
- `gui_main_monitoring_preview.py`의 독립 시안용 status bar는 메인 하단 QLabel과 연결되지 않아 제외했다.
- Production caller가 없는 함수의 문구는 아래 Dead Code 절에 분리하고 Production 총계에서 제외했다.

## 실제 도달 경계

`MainWindow._bind_main_status_message_to_button_row()`가 hidden `QStatusBar.messageChanged`를
`main_status_message_label.setText`에 연결한다. 직접 QLabel writer는 이 연결과 최초
`status_bar.currentMessage()` 동기화뿐이다.

실제 진입점은 다음 네 가지다.

1. `MainWindow.statusBar().showMessage(...)`
2. `MainMonitoringStockOperationAdapter.statusBarMessage()`
3. `AutoTradeOperationHost.statusBarMessage()`
4. `AutoTradeSettingWindow.statusBarMessage()` -> `persistent_feature_owner(self)` -> MainWindow

## Production 원문 목록

### 초기화 / 로그인 / 서버인증 / Recovery

| 번호 | 실제 문자열 또는 Template | 발생 조건과 동적 값 / 대표 예시 | Caller 함수 | 파일 | 분류 |
|---:|---|---|---|---|---|
| 1 | `준비 완료` | 메인 UI 초기화 완료 | `MainWindow._setup_ui` | `gui_windows.py:4134` | 초기화/한글 |
| 2 | `운영 재개 승인이 취소되었습니다.` | Startup Recovery 승인을 사용자가 취소 | `MainWindow.review_startup_recovery` | `gui_windows.py:7265` | 서버인증/운영 시작·정지/한글 |
| 3 | `운영 재개 승인 완료: {status}` | `status`: Recovery 상태. 예: `운영 재개 승인 완료: READY` | `MainWindow.review_startup_recovery` | `gui_windows.py:7279` | 서버인증/운영 시작·정지/한글·혼합/동적 |
| 4 | `키움 OpenAPI를 사용할 수 없습니다. 설치 상태와 32비트 실행 환경을 확인하십시오.` | API 객체가 없거나 사용 불가 | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7281-7305` | 로그인/오류·경고/한글·혼합 |
| 5 | `로그인 상태: 연결됨` | 수동 로그인에서 이미 연결됨, 연결 결과 또는 login-state fallback | `MainWindow.login_kiwoom_manually`, `MainWindow.on_kiwoom_login_state_changed` | `gui_windows.py:7314,7335,7369-7428` | 로그인/연결/한글 |
| 6 | `키움 로그인 요청 중 오류가 발생했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | `api.login()` 예외 | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7319-7327` | 로그인/오류·경고/한글·혼합 |
| 7 | `로그인 요청됨` | 로그인 요청 접수 또는 진행 중 | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7330-7348` | 로그인/한글 |
| 8 | `키움 로그인 요청을 완료하지 못했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | 로그인 요청 결과가 pending/connected 아님 | `MainWindow.login_kiwoom_manually` | `gui_windows.py:7336-7348` | 로그인/오류·경고/한글·혼합 |
| 9 | `login succeeded` | `OnEventConnect(0)` 성공 | `KiwoomApi._on_event_connect` -> `MainWindow.on_kiwoom_login_state_changed` | `kiwoom_api.py:2906-2914`, `gui_windows.py:7350-7428` | 로그인/연결/영문/개발·디버그 |
| 10 | `user info exchange failed` | OpenAPI 오류 `-100` | 같은 login-state signal 경로 | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` | 로그인/오류·경고/영문/개발·디버그 |
| 11 | `server connection failed` | OpenAPI 오류 `-101` | 같은 login-state signal 경로 | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` | 로그인/연결/오류·경고/영문/개발·디버그 |
| 12 | `version processing failed` | OpenAPI 오류 `-102` | 같은 login-state signal 경로 | `kiwoom_api.py:2918-2930`, `gui_windows.py:7428` | 로그인/오류·경고/영문/개발·디버그 |
| 13 | `login failed: {code}` | `code`: 기타 `OnEventConnect` 오류코드. 예: `login failed: -999` | 같은 login-state signal 경로 | `kiwoom_api.py:2923-2930`, `gui_windows.py:7428` | 로그인/오류·경고/영문/개발·디버그/동적 |
| 14 | `kiwoom api disconnected` | 연결된 세션의 연결해제 관측 | `KiwoomApi._observe_connected_state` -> `MainWindow.on_kiwoom_login_state_changed` | `kiwoom_api.py:1037-1089`, `gui_windows.py:7428` | 로그인/연결/영문/개발·디버그 |
| 15 | `미연결 상태` | 로그인 bootstrap 창이 연결 없이 닫힘 | `KiwoomApi._observe_login_bootstrap_desktop` -> login-state signal | `kiwoom_api.py:1748-1770`, `gui_windows.py:7428` | 로그인/연결/한글 |
| 16 | `로그인 상태: 실패` | login-state payload에 message가 없는 disconnected fallback | `MainWindow.on_kiwoom_login_state_changed` | `gui_windows.py:7370-7428` | 로그인/연결/오류·경고/한글 |
| 17 | `계좌비밀번호 입력 기능을 사용할 수 없습니다.` | 계좌비밀번호 입력 API가 없음 | `MainWindow.open_current_account_authentication` | `gui_windows.py:7556-7567` | 서버인증/오류·경고/한글 |
| 18 | `계좌비밀번호 입력창을 열지 못했습니다.` | 계좌비밀번호 입력창 호출 실패 | `MainWindow.open_current_account_authentication` | `gui_windows.py:7556-7571` | 서버인증/오류·경고/한글 |
| 19 | `{action}할 수 없습니다. 로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오.[\n\n원인: {detail}]` | `action`: 요청 동작, `detail`: 외부 노출 가능한 reason detail. 예: `운영시작할 수 없습니다. 로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오.` | `startup_recovery_operation_block_message`, setting/main recovery guard | `gui_auto_trade_run_control.py:1128-1137`, `gui_auto_trade_setting_window.py:5467`, `gui_main_stock_context_menu.py:546` | 서버인증/오류·경고/한글·혼합/동적 |
| 20 | `서버 연결 및 계좌 복구 확인 후 사용할 수 있습니다.` | 긴급정지/정지해제 preflight 미완료 | `execute_emergency_stop`, `execute_selected_emergency_stop` | `gui_main_emergency_ops.py:70,765,931` | 서버인증/오류·경고/한글 |
| 21 | `키움 서버에 로그인되어 있지 않습니다.` | Recovery context/routine guard에서 미로그인 | `MainWindow.production_recovery_block_user_message`, `MainWindow.routine_recovery_block_message` | `gui_windows.py:6895-7045` | 로그인/서버인증/오류·경고/한글 |
| 22 | `로그인 세션 정보를 확인할 수 없습니다. 키움 서버에 다시 로그인하십시오.` | login session id 없음 | `MainWindow.production_recovery_block_user_message` | `gui_windows.py:6960-6968` | 로그인/서버인증/오류·경고/한글 |
| 23 | `운영할 계좌를 선택하십시오.` | 선택 계좌 없음 | 같은 함수 | `gui_windows.py:6969-6970` | 서버인증/오류·경고/한글 |
| 24 | `Recovery 데이터를 읽을 수 없습니다. 복구를 다시 실행한 후 운영을 시작하십시오.` | registry evidence read 오류 | 같은 함수 | `gui_windows.py:6971-6977` | 서버인증/오류·경고/한글·혼합 |
| 25 | `운영 시작에 필요한 Recovery 정보를 확인할 수 없습니다. 로그인과 계좌 선택 상태를 확인한 후 Recovery를 다시 실행하십시오.` | Recovery context missing의 일반 fallback | 같은 함수 | `gui_windows.py:6978-6982` | 서버인증/운영 시작·정지/오류·경고/한글·혼합 |
| 26 | `운영 시작 전에 Recovery가 완료되지 않았습니다. 로그인과 계좌 선택 후 Recovery를 완료하십시오.` | `RECOVERY_NOT_STARTED` | 같은 함수 | `gui_windows.py:6984-6988` | 서버인증/운영 시작·정지/오류·경고/한글·혼합 |
| 27 | `Recovery가 진행 중입니다. 복구가 완료된 후 다시 시도하십시오.` | `RECOVERY_IN_PROGRESS` | 같은 함수 | `gui_windows.py:6989-6991` | 서버인증/한글·혼합 |
| 28 | `복구가 필요한 종목이 남아 있습니다. 검토관리에서 해당 종목을 처리하십시오.` | account review required | 같은 함수 | `gui_windows.py:6992-6995` | 서버인증/오류·경고/한글 |
| 29 | `현재 로그인 또는 계좌와 Recovery 정보가 일치하지 않습니다. Recovery를 다시 실행하십시오.` | identity mismatch | 같은 함수 | `gui_windows.py:6996-6999` | 로그인/서버인증/오류·경고/한글·혼합 |
| 30 | `이전 Recovery 정보는 현재 세션에서 사용할 수 없습니다. Recovery를 다시 실행하십시오.` | stale recovery session | 같은 함수 | `gui_windows.py:7000-7003` | 서버인증/오류·경고/한글·혼합 |
| 31 | `선택한 종목의 Recovery가 아직 완료되지 않았습니다.` | stock recovery pending | 같은 함수 | `gui_windows.py:7004-7006` | 서버인증/오류·경고/한글·혼합 |
| 32 | `선택한 종목은 복구 검토 대상입니다. 검토관리에서 해당 종목을 처리하십시오.` | stock recovery review required | 같은 함수 | `gui_windows.py:7007-7010` | 서버인증/오류·경고/한글 |
| 33 | `선택한 종목의 Recovery에 실패했습니다. 검토관리에서 상태를 확인하십시오.` | stock recovery failed | 같은 함수 | `gui_windows.py:7011-7014` | 서버인증/오류·경고/한글·혼합 |
| 34 | `Runtime 데이터를 읽을 수 없어 Recovery에 실패했습니다. 검토관리에서 Runtime 상태를 확인하십시오.` | damaged runtime | 같은 함수 | `gui_windows.py:7018-7024` | 서버인증/오류·경고/한글·혼합 |
| 35 | `계좌의 보유 또는 미체결 정보를 확인하지 못했습니다. 키움 연결 상태를 확인한 후 Recovery를 다시 실행하십시오.` | broker snapshot 불완전/adapter 없음 | 같은 함수 | `gui_windows.py:7025-7035` | 서버인증/연결/오류·경고/한글·혼합 |
| 36 | `운영 주기 실행을 시작하지 못했습니다. 로그를 확인한 후 Recovery를 다시 실행하십시오.` | recovery timer start 실패 | 같은 함수 | `gui_windows.py:7036-7040` | 서버인증/오류·경고/한글·혼합 |
| 37 | `Recovery가 완료된 운영 대상 종목이 없습니다. 검토관리에서 종목 상태를 확인하십시오.` | restored stock 없음 | 같은 함수 | `gui_windows.py:7041-7045` | 서버인증/오류·경고/한글·혼합 |
| 38 | `계좌 Recovery에 실패했습니다. 로그인과 계좌 상태를 확인한 후 Recovery를 다시 실행하십시오.` | account recovery failure 일반 fallback | 같은 함수 | `gui_windows.py:7046-7050` | 로그인/서버인증/오류·경고/한글·혼합 |
| 39 | `{action} 불가\n\n사용할 계좌 정보가 아직 확인되지 않았습니다.\n로그인과 계좌 선택 상태를 확인해 주세요.` | `action`: 루틴 작업명. 예: `루틴 재시작 불가...` | `MainWindow.routine_recovery_block_message` -> `MainMonitoringStockOperationAdapter.require_startup_recovery_session` | `gui_windows.py:6895-6937`, `gui_main_stock_context_menu.py:531-549` | 서버인증/오류·경고/한글/동적 |
| 40 | `{action} 불가\n\n프로그램 시작 후 기존 운영 상태를 확인하고 있습니다.\n확인이 끝난 뒤 다시 시도해 주세요.` | Recovery `COLLECTING/RECONCILING`. 예: `루틴 재시작 불가...` | 같은 경로 | 같은 파일 | 서버인증/운영 시작·정지/한글/동적 |
| 41 | `{action} 불가\n\n이전 운영 상태를 확인하지 못했습니다.\n운영 상태와 로그를 확인해 주세요.` | Recovery `FAILED/STALE` | 같은 경로 | 같은 파일 | 서버인증/운영 시작·정지/오류·경고/한글/동적 |
| 42 | `{action} 불가\n\n확인이 필요한 운영 항목이 남아 있습니다.\n검토관리에서 상태를 확인해 주세요.` | Recovery `REVIEW_REQUIRED` | 같은 경로 | 같은 파일 | 서버인증/운영 시작·정지/오류·경고/한글/동적 |
| 43 | `{action} 불가\n\n프로그램 시작 후 운영 상태 확인이 아직 완료되지 않았습니다.\n잠시 후 다시 시도해 주세요.` | 기타 recovery 미완료 | 같은 경로 | 같은 파일 | 서버인증/운영 시작·정지/오류·경고/한글/동적 |

### 메인 운영 / 선택 / 상태 / Operation Start

| 번호 | 실제 문자열 또는 Template | 발생 조건과 동적 값 / 대표 예시 | Caller 함수 | 파일 | 분류 |
|---:|---|---|---|---|---|
| 44 | `선택한 루틴 정보를 읽을 수 없습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.` | routine instance lookup 실패 | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10597-10613` | 운영 시작·정지/오류·경고/한글 |
| 45 | `선택한 루틴에 등록된 종목이 없습니다.\n자동매매 설정에서 종목을 등록하십시오.` | 선택 루틴에 등록 종목 없음 | 같은 함수 | `gui_windows.py:10662-10671` | 운영 시작·정지/예산·설정/오류·경고/한글 |
| 46 | `{instance} {requested_action} 완료 (대상 {count}종목)` | instance/action/count. 예: `기본루틴 운영시작 완료 (대상 5종목)` | 같은 함수 | `gui_windows.py:10727-10738` | 운영 시작·정지/한글/동적 |
| 47 | `{instance} {requested_action} 실패: {user_message}` | backend/adapter user message 삽입. 예: `기본루틴 운영시작 실패: 키움 서버에 로그인되어 있지 않습니다.` | 같은 함수 | `gui_windows.py:10741-10752` | 운영 시작·정지/오류·경고/한글/동적 |
| 48 | `{scope} {command}: {display_name} / 대상 0` | scope=`그룹/카테고리`, command=`조기마감/즉시청산`. 예: `그룹 조기마감: 그룹1 / 대상 0` | `MainWindow.request_routine_definition_operation` | `gui_windows.py:11185-11227` | 운영 시작·정지/한글/동적 |
| 49 | `{scope} {command} 취소: {display_name}` | 상위 scope 명령 확인 취소. 예: `그룹 조기마감 취소: 그룹1` | 같은 함수 | `gui_windows.py:11261` | 운영 시작·정지/한글/동적 |
| 50 | `{scope} {command}: {display_name} / 성공 {applied} / 차단 {failed}` | 예: `그룹 조기마감: 그룹1 / 성공 3 / 차단 1` | 같은 함수 | `gui_windows.py:11319` | 운영 시작·정지/한글/동적 |
| 51 | `루틴 {command}: {display_name} / 대상 0` | 예: `루틴 조기마감: 기본루틴 / 대상 0` | `MainWindow.request_routine_operation` | `gui_windows.py:11346-11365` | 운영 시작·정지/한글/동적 |
| 52 | `루틴 {command} 취소: {display_name}` | 예: `루틴 즉시청산 취소: 기본루틴` | 같은 함수 | `gui_windows.py:11391` | 운영 시작·정지/한글/동적 |
| 53 | `루틴 {command}: {display_name} / 성공 {applied} / 차단 {failed}` | 예: `루틴 조기마감: 기본루틴 / 성공 2 / 차단 0` | 같은 함수 | `gui_windows.py:11447` | 운영 시작·정지/한글/동적 |
| 54 | `관제창 조기마감: 대상 0` | 현재 표시 종목에 대상 없음 | `MainWindow.request_visible_monitoring_early_close` | `gui_windows.py:10492-10497` | 운영 시작·정지/한글 |
| 55 | `관제창 조기마감 취소` | 확인 취소 | 같은 함수 | `gui_windows.py:10529` | 운영 시작·정지/한글 |
| 56 | `관제창 조기마감: 성공 {applied} / 차단 {failed}` | 예: `관제창 조기마감: 성공 4 / 차단 1` | 같은 함수 | `gui_windows.py:10580` | 운영 시작·정지/한글/동적 |
| 57 | `거래권한을 변경할 종목을 1개 이상 선택하세요.` | 거래권한 변경 선택 없음 | `AutoTradeSettingWindow.toggle_selected_trade_permission`, `MainMonitoringStockOperationAdapter.toggle_selected_trade_permission` | `gui_auto_trade_setting_window.py:5945-5951`, `gui_main_stock_context_menu.py:433-436` | 예산·설정/오류·경고/한글 |
| 58 | `거래권한 변경: {changed}개[ / 차단 {blocked}개]` | 예: `거래권한 변경: 3개 / 차단 1개` | 같은 두 함수 | `gui_auto_trade_setting_window.py:5975-5980`, `gui_main_stock_context_menu.py:455-460` | 예산·설정/한글/동적 |
| 59 | `운영 중에는 더블클릭으로 운영 대상을 변경할 수 없습니다. 우클릭 운영시작을 사용하세요.` | current-running 종목 운영제외 mutation 차단 | `auto_trade_set_stock_operation_exclusion`, double-click guard | `gui_auto_trade_status_ops.py:82,215-217`, `gui_auto_trade_setting_window.py:3838-3842` | 운영 시작·정지/오류·경고/한글 |
| 60 | `{code} {name} {label}` | label=`운영 제외/운영 제외 해제`. 예: `005930 삼성전자 운영 제외` | `auto_trade_set_stock_operation_exclusion` | `gui_auto_trade_status_ops.py:239-253` | 예산·설정/한글/동적 |
| 61 | `현재 루틴 전체 종목 선택: {row_count}개` | 예: `현재 루틴 전체 종목 선택: 5개` | `select_all_current_routine_stocks` | `gui_auto_trade_selection.py:65-68` | 기타/한글/동적 |
| 62 | `현재 루틴 종목 선택 해제` | 선택해제 | `clear_current_routine_stock_selection` | `gui_auto_trade_selection.py:71-74` | 기타/한글 |
| 63 | `루틴 등록해제 완료: {count}개` | 예: `루틴 등록해제 완료: 2개` | `unregister_selected_auto_trade_stocks` | `gui_auto_trade_unregister.py:68-145` | 예산·설정/한글/동적 |
| 64 | `{label} 전환 완료: {count}개` | 수동 override label/count. 예: `즉시적용 전환 완료: 2개` | `AutoTradeSettingWindow.toggle_selected_manual_override_flag` | `gui_auto_trade_setting_window.py:5989-6036` | 예산·설정/한글/동적 |
| 65 | `수동운영 기본 리셋 완료: {count}개` | 예: `수동운영 기본 리셋 완료: 2개` | `AutoTradeSettingWindow.reset_selected_manual_override` | `gui_auto_trade_setting_window.py:6041-6079` | 예산·설정/한글/동적 |
| 66 | `오늘 운영이 종료되었습니다.` | global status `NORMAL_ENDED` | `AutoTradeSettingWindow.start_selected_auto_trades` | `gui_auto_trade_setting_window.py:12938-12942` | 운영 시작·정지/오류·경고/한글 |
| 67 | `운영시작 대상이 없습니다. 운영 제외를 해제한 뒤 다시 시도하세요.` | 선택/startable/running 대상 없음 | 같은 함수 | `gui_auto_trade_setting_window.py:12960-12964` | 운영 시작·정지/오류·경고/한글 |
| 68 | `운영 상태를 변경하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | routine instance start backend 예외 | `MainWindow.toggle_routine_instance_operation` | `gui_windows.py:10687-10707` | 운영 시작·정지/오류·경고/한글 |
| 69 | `전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오.` | Operation Start 후 global state write 실패 | `auto_trade_start_selected_auto_trades` | `gui_auto_trade_run_control.py:2927,2933` | 운영 시작·정지/오류·경고/한글 |
| 70 | `운영 시작 {started}개[ · 검토 제외 {review}개][ · 설정 제외 {validation}개][ · 실패 {failed}개]` | batch result. 예: `운영 시작 2개 · 검토 제외 1개 · 실패 1개` | `_start_result_summary` -> `_show_operation_start_summary_toast` status fallback | `gui_auto_trade_run_control.py:1757-1769,2913-2921` | 운영 시작·정지/한글/동적 |
| 71 | `대상종목 {requested}  \|  기운영중 {already}  \|  운영시작 {started}  \|  운영불가 {unavailable}[\n{reason_label} {count} · ...]` | `result.user_message`가 비어 있는 partial-result fallback. 예: `대상종목 5  \|  기운영중 1  \|  운영시작 2  \|  운영불가 2` | `operation_start_result_summary_toast_text` -> `_show_operation_start_summary_toast` | `gui_auto_trade_run_control.py:1501-1572` | 운영 시작·정지/한글/동적 |
| 72 | `{name_or_code} 운영을 시작했습니다.` | 단일 종목 성공. 예: `삼성전자 운영을 시작했습니다.` | `_apply_start_request_context` -> `auto_trade_start_selected_auto_trades` | `gui_auto_trade_run_control.py:1748,2925` | 운영 시작·정지/한글/동적 |
| 73 | `{stock}은/는 긴급정지 상태입니다.` | stock/받침 조사. 예: `삼성전자는 긴급정지 상태입니다.` | `_single_start_failure_user_message` | `gui_auto_trade_run_control.py:1626-1752` | 운영 시작·정지/오류·경고/한글/동적 |
| 74 | `{stock}은/는 검토관리 대상입니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | 단일 review block | 같은 함수 | 같은 파일 | 운영 시작·정지/오류·경고/한글/동적 |
| 75 | `{stock}은/는 이미 운영 중입니다.` | 단일 already-running | 같은 함수 | 같은 파일 | 운영 시작·정지/한글/동적 |
| 76 | `{stock}의 필수 운영 설정이 완료되지 않았습니다.\n자동매매 설정을 확인한 뒤 다시 시도하십시오.` | 단일 missing settings | 같은 함수 | 같은 파일 | 운영 시작·정지/예산·설정/오류·경고/한글/동적 |
| 77 | `{stock}의 현재 세션 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.` | 단일 starting budget unresolved | 같은 함수 | 같은 파일 | 운영 시작·정지/예산·설정/오류·경고/한글/동적 |
| 78 | `{stock}의 초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.` | 단일 invalid initial quantity | 같은 함수 | 같은 파일 | 운영 시작·정지/예산·설정/오류·경고/한글/동적 |
| 79 | `{stock}의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.` | 단일 runtime missing/damaged | 같은 함수 | 같은 파일 | 운영 시작·정지/오류·경고/한글·혼합/동적 |
| 80 | `{stock}의 운영 상태를 저장하거나 다시 확인하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | 단일 state save/readback 실패 | 같은 함수 | 같은 파일 | 운영 시작·정지/오류·경고/한글/동적 |
| 81 | `{stock}의 Recovery가 아직 완료되지 않았습니다.\n복구가 완료된 뒤 다시 시도하십시오.` | 단일 recovery pending | 같은 함수 | 같은 파일 | 서버인증/운영 시작·정지/오류·경고/한글·혼합/동적 |
| 82 | `{stock}의 Recovery에 실패했습니다.\n검토관리에서 상태를 확인하십시오.` | 단일 recovery failed | 같은 함수 | 같은 파일 | 서버인증/운영 시작·정지/오류·경고/한글·혼합/동적 |
| 83 | `오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오.` | same-day normal end | `_single_start_failure_user_message`, global start guard | `gui_auto_trade_run_control.py:1678-1680,2149-2151` | 운영 시작·정지/오류·경고/한글 |
| 84 | `{stock}은/는 시간운영 종료로 운영을 시작할 수 없습니다.` | final session ended | `_single_start_failure_user_message` | `gui_auto_trade_run_control.py:1681-1683` | 운영 시작·정지/오류·경고/한글/동적 |
| 85 | `{stock}은/는 현재 운영시작 가능 시간이 아닙니다.` | outside operation start time | 같은 함수 | `gui_auto_trade_run_control.py:1684-1685` | 운영 시작·정지/오류·경고/한글/동적 |
| 86 | `{stock}은/는 보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.` | holding exists | 같은 함수 | `gui_auto_trade_run_control.py:1686-1687` | 운영 시작·정지/오류·경고/한글/동적 |
| 87 | `{stock}은/는 미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.` | pending order family | 같은 함수 | `gui_auto_trade_run_control.py:1688-1689` | 운영 시작·정지/오류·경고/한글/동적 |
| 88 | `{stock}은/는 취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.` | pending cancel | 같은 함수 | `gui_auto_trade_run_control.py:1690-1691` | 운영 시작·정지/오류·경고/한글/동적 |
| 89 | `{stock}은/는 마감 또는 청산 절차가 진행 중입니다.` | close/liquidation active | 같은 함수 | `gui_auto_trade_run_control.py:1692-1693` | 운영 시작·정지/오류·경고/한글/동적 |
| 90 | `{stock}의 운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | target classification/internal exception | 같은 함수 | `gui_auto_trade_run_control.py:1694-1698` | 운영 시작·정지/오류·경고/한글/동적 |
| 91 | `{stock}은/는 현재 운영을 시작할 수 없는 상태입니다.\n자동매매 설정과 검토관리 상태를 확인하십시오.` | 단일 start 일반 fallback | 같은 함수 | `gui_auto_trade_run_control.py:1701-1705` | 운영 시작·정지/오류·경고/한글/동적 |
| 92 | `모든 종목이 긴급정지 상태입니다.` | 전체 emergency | `_start_failure_user_message` | `gui_auto_trade_run_control.py:1319-1321` | 운영 시작·정지/오류·경고/한글 |
| 93 | `모든 등록 종목이 검토 대상으로 분리되어 있습니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | `all_review=True` | 같은 함수 | `gui_auto_trade_run_control.py:1337-1341` | 운영 시작·정지/오류·경고/한글 |
| 94 | `선택한 루틴은 이미 운영 중입니다.` | all already-running routine | 같은 함수 | `gui_auto_trade_run_control.py:1342-1343` | 운영 시작·정지/한글 |
| 95 | `현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.` | 전체 starting budget unresolved | 같은 함수 | `gui_auto_trade_run_control.py:1344-1348` | 운영 시작·정지/예산·설정/오류·경고/한글 |
| 96 | `초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.` | 전체 invalid initial quantity | 같은 함수 | `gui_auto_trade_run_control.py:1349-1353` | 운영 시작·정지/예산·설정/오류·경고/한글 |
| 97 | `모든 등록 종목의 필수 설정이 완료되지 않았습니다.\n자동매매 설정을 확인하십시오.` | 전체 missing required settings | 같은 함수 | `gui_auto_trade_run_control.py:1354-1358` | 운영 시작·정지/예산·설정/오류·경고/한글 |
| 98 | `모든 등록 종목이 검토 대상으로 분리되었습니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | reasons가 review only | 같은 함수 | `gui_auto_trade_run_control.py:1359-1363` | 운영 시작·정지/오류·경고/한글 |
| 99 | `현재는 매매 운영 시간이 아닙니다.` | outside operation time only | 같은 함수 | `gui_auto_trade_run_control.py:1366-1367` | 운영 시작·정지/오류·경고/한글 |
| 100 | `보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.` | batch holding exists | 같은 함수 | `gui_auto_trade_run_control.py:1368-1369` | 운영 시작·정지/오류·경고/한글 |
| 101 | `미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.` | batch pending order | 같은 함수 | `gui_auto_trade_run_control.py:1370-1371` | 운영 시작·정지/오류·경고/한글 |
| 102 | `취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.` | batch pending cancel | 같은 함수 | `gui_auto_trade_run_control.py:1372-1373` | 운영 시작·정지/오류·경고/한글 |
| 103 | `마감 또는 청산 절차가 진행 중이어서 운영을 다시 시작할 수 없습니다.` | batch close/liquidation active | 같은 함수 | `gui_auto_trade_run_control.py:1374-1375` | 운영 시작·정지/오류·경고/한글 |
| 104 | `종목의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.` | batch runtime missing/damaged | 같은 함수 | `gui_auto_trade_run_control.py:1376-1380` | 운영 시작·정지/오류·경고/한글·혼합 |
| 105 | `종목의 운영 상태를 저장하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | batch state save 실패 | 같은 함수 | `gui_auto_trade_run_control.py:1381-1385` | 운영 시작·정지/오류·경고/한글 |
| 106 | `운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | batch internal exception | 같은 함수 | `gui_auto_trade_run_control.py:1386-1390` | 운영 시작·정지/오류·경고/한글 |
| 107 | `현재 운영을 시작할 수 있는 종목이 없습니다.\n검토관리와 자동매매 설정을 확인하십시오.` | batch 일반 fallback | 같은 함수 | `gui_auto_trade_run_control.py:1391-1395` | 운영 시작·정지/오류·경고/한글 |
| 108 | `[운영중 유지: {already}종목\n]{reason_label}: {count}종목[\n- {stock_label}...]` | reason별 그룹 동적 조합. 예: `운영중 유지: 1종목\n시간운영 종료: 2종목\n- 002810 삼영무역` | `_blocked_target_groups_message` | `gui_auto_trade_run_control.py:1418-1461` | 운영 시작·정지/오류·경고/한글/동적 |
| 109 | `운영 시작 대상을 확인하는 중 오류가 발생했습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.` | target collection 또는 classification 예외 | `auto_trade_start_selected_auto_trades` | `gui_auto_trade_run_control.py:2084-2091,2307-2313` | 운영 시작·정지/오류·경고/한글 |
| 110 | `운영 시작 전 서버와 계좌 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | global prerequisite checker 예외 | `_global_start_prerequisite_result` | `gui_auto_trade_run_control.py:1148-1158` | 서버인증/운영 시작·정지/오류·경고/한글 |
| 111 | `전역 긴급정지 상태입니다. 정지해제 후 운영시작을 다시 시도하십시오.` | global emergency stop active | `auto_trade_start_selected_auto_trades` | `gui_auto_trade_run_control.py:2184-2188` | 운영 시작·정지/오류·경고/한글 |
| 112 | `운영을 시작할 종목을 1개 이상 선택하십시오.` | selected targets 없음 | 같은 함수 | `gui_auto_trade_run_control.py:2258-2260` | 운영 시작·정지/오류·경고/한글 |
| 113 | `복구 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | recovery filter 예외 | 같은 함수 | `gui_auto_trade_run_control.py:2429-2435` | 서버인증/운영 시작·정지/오류·경고/한글·혼합 |
| 114 | `선택한 종목이 모두 이미 운영 중입니다.` | selected targets가 전부 current-running | `auto_trade_start_rows_auto_trades`, `AutoTradeSettingWindow.start_selected_auto_trades` | `gui_auto_trade_run_control.py:3017-3019`, `gui_auto_trade_setting_window.py:12968-12970` | 운영 시작·정지/한글 |

### 긴급정지 / ATS / 마감 / 청산

| 번호 | 실제 문자열 또는 Template | 발생 조건과 동적 값 / 대표 예시 | Caller 함수 | 파일 | 분류 |
|---:|---|---|---|---|---|
| 115 | `전역 긴급정지 기록에 실패했습니다. 종목별 긴급정지를 시작하지 않았습니다.` | global emergency write 실패 | `execute_emergency_stop` | `gui_main_emergency_ops.py:791-793` | 운영 시작·정지/오류·경고/한글 |
| 116 | `긴급정지 실행 완료: {changed}개 종목[ / 기존 검토 유지 {preserved}개][ / 실패 {failed}개 / 전역 차단 유지]` | 예: `긴급정지 실행 완료: 4개 종목 / 기존 검토 유지 1개` | 같은 함수 | `gui_main_emergency_ops.py:878-885` | 운영 시작·정지/한글/동적 |
| 117 | `종목 검토정지: 변경 {changed}개[ / 이미 검토관리 {skipped}개][ / 실패 {failed}개]` | selected review stop 결과 | `execute_selected_emergency_stop` | `gui_main_emergency_ops.py:1004-1011` | 운영 시작·정지/한글/동적 |
| 118 | `전체 긴급정지 상태에서는 상단 정지해제를 사용하십시오.` | global emergency 중 개별 정지해제 시도 | `execute_selected_emergency_release` | `gui_main_emergency_ops.py:1119-1123` | 운영 시작·정지/오류·경고/한글 |
| 119 | `종목 정지해제: 정상 {normal}개 / 검토관리 유지 {review}개[ / 긴급정지 유지 {blocked}개][ / 대상아님 {skipped}개][ / 실패 {failed}개]` | 예: `종목 정지해제: 정상 2개 / 검토관리 유지 1개` | 같은 함수 | `gui_main_emergency_ops.py:1179-1187` | 운영 시작·정지/한글/동적 |
| 120 | `전체 정지해제 차단 \| {operator_reason}` | `operator_reason`: preflight 사용자 사유. 예: `전체 정지해제 차단 \| 계좌 복구 확인 필요` | `release_emergency_stop` | `gui_main_emergency_ops.py:1277-1279` | 운영 시작·정지/오류·경고/한글/동적 |
| 121 | `정지해제 완료: 정상 {normal}개 / 검토관리 {review}개` | 예: `정지해제 완료: 정상 4개 / 검토관리 1개` | 같은 함수 | `gui_main_emergency_ops.py:1363-1374` | 운영 시작·정지/한글/동적 |
| 122 | `정지해제 미완료: 정상 {normal}개 / 검토관리 {review}개 / 긴급정지 잔존 {remaining}개 / 실패 {failed}개 / 전역 차단 유지` | 잔존 또는 실패 존재 | 같은 함수 | `gui_main_emergency_ops.py:1363-1374` | 운영 시작·정지/오류·경고/한글/동적 |
| 123 | `ATS 주문방식 설정 오류: INVALID_ATS_EXECUTION_METHOD / {codes}` | `codes`: invalid 종목코드 목록. 예: `... / 005930, 000660` | `auto_trade_selected_manual_ats_execution_method_state` | `gui_auto_trade_ats_ops.py:91-116` | 예산·설정/오류·경고/개발·디버그/한글·혼합/동적 |
| 124 | `ATS설정 변경 완료: {label} {ON_or_OFF} / {changed}개` | 예: `ATS설정 변경 완료: 장후 ON / 2개` | `auto_trade_set_selected_manual_ats_flag` | `gui_auto_trade_ats_ops.py:349-358` | 예산·설정/한글·혼합/동적 |
| 125 | `ATS 주문방식 저장 실패: INVALID_ATS_EXECUTION_METHOD` | requested method invalid | `auto_trade_set_selected_manual_ats_execution_method` | `gui_auto_trade_ats_ops.py:361-376` | 예산·설정/오류·경고/개발·디버그/한글·혼합 |
| 126 | `ATS 주문방식 변경 완료: {method_label} / {succeeded}개` | 예: `ATS 주문방식 변경 완료: 시장가 / 2개` | 같은 함수 | `gui_auto_trade_ats_ops.py:386-388` | 예산·설정/한글·혼합/동적 |
| 127 | `ATS {method}매도 취소` | ATS liquidation 확인 취소. 예: `ATS 시장가매도 취소` | `auto_trade_execute_selected_manual_ats_liquidation` | `gui_auto_trade_ats_ops.py:865-992` | 운영 시작·정지/한글·혼합/동적 |
| 128 | `ATS {method}매도 SendOrder 접수 기록: {count}개` | 예: `ATS 시장가매도 SendOrder 접수 기록: 2개` | 같은 함수 | `gui_auto_trade_ats_ops.py:1040-1042` | SendOrder/개발·디버그/한글·혼합/동적 |
| 129 | `ATS {method}매도 취소 확인 대기: {count}개` | pending cancellation | 같은 함수 | `gui_auto_trade_ats_ops.py:1044-1046` | 운영 시작·정지/한글·혼합/동적 |
| 130 | `ATS {method}매도 청산 대상 없음: {count}개` | no holding | 같은 함수 | `gui_auto_trade_ats_ops.py:1048-1050` | 운영 시작·정지/한글·혼합/동적 |
| 131 | `개별청산 설정 완료: {minutes}분/{method} / 대상 {count}개` | 예: `개별청산 설정 완료: 5분/시장가 / 대상 2개` | `auto_trade_apply_selected_individual_liquidation_method` | `gui_auto_trade_close.py:858-1004` | 예산·설정/운영 시작·정지/한글/동적 |
| 132 | `현재 상태는 마감정책 취소 대상이 아닙니다.` | 메인 관제 adapter에서 조기마감 취소 불가 | `auto_trade_cancel_selected_early_close` | `gui_auto_trade_close.py:1353-1425` | 운영 시작·정지/오류·경고/한글 |
| 133 | `마감정책이 취소되었습니다.` | 메인 관제 adapter에서 조기마감 취소 성공 | 같은 함수 | 같은 파일 | 운영 시작·정지/한글 |
| 134 | `조기마감 불가: 청산 진행 중` | active liquidation 존재 | `auto_trade_apply_selected_early_close` | `gui_auto_trade_close.py:1525` | 운영 시작·정지/오류·경고/한글 |
| 135 | `조기마감 적용: 0개` | eligible target 없음 | 같은 함수 | `gui_auto_trade_close.py:1530` | 운영 시작·정지/한글 |
| 136 | `조기마감 취소` | 확인 취소 | 같은 함수 | `gui_auto_trade_close.py:1573` | 운영 시작·정지/한글 |
| 137 | `조기마감 적용: {completed}개[ / 제외 {skipped}개]` | 예: `조기마감 적용: 3개 / 제외 1개` | 같은 함수 | `gui_auto_trade_close.py:1753-1757` | 운영 시작·정지/한글/동적 |

### Timer / 데이터 / 환경설정 / 예외

| 번호 | 실제 문자열 또는 Template | 발생 조건과 동적 값 / 대표 예시 | Caller 함수 | 파일 | 분류 |
|---:|---|---|---|---|---|
| 138 | `Runtime 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | runtime refresh timer 예외 | `AutoTradeSettingWindow.on_runtime_file_timer_tick` | `gui_auto_trade_setting_window.py:5404-5416` | 오류·경고/한글·혼합 |
| 139 | `시간정책 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | time policy timer 예외 | `AutoTradeSettingWindow.on_time_policy_timer_tick` | `gui_auto_trade_setting_window.py:5418-5428` | 오류·경고/한글·혼합 |
| 140 | `자동매매 운영 주기를 처리하지 못했습니다. 로그를 확인하십시오.` | main operation host cycle 예외 | `AutoTradeOperationHost._on_operation_cycle_timeout` | `gui_auto_trade_operation_host.py:548-550` | 운영 시작·정지/오류·경고/한글 |
| 141 | `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors} / 후보 {created} / 승인검사 {approval_checked} / 승인 {approved}` | signal pipeline summary. 예: `... 확인 5 / 차단 1 / 허용 4 / 오류 0 / 후보 2 / 승인검사 2 / 승인 1` | `_process_pending_signal_pipeline` | `gui_auto_trade_timer.py:148-204` | Queue/개발·디버그/한글/동적 |
| 142 | `실자동매매 주문처리: 실행 {processed} / 차단 {blocked}` | auto real-order activity | `_process_pending_signal_pipeline` | `gui_auto_trade_timer.py:238-240` | SendOrder/개발·디버그/한글/동적 |
| 143 | `루틴 신호 로그: 기록 {logged}개[ / 오류 {errors}개]` | signal probe log activity | `_auto_trade_run_signal_cycle` | `gui_auto_trade_timer.py:244-276` | 개발·디버그/오류·경고/한글/동적 |
| 144 | `주문 후보를 검증하는 중 오류가 발생했습니다. 로그를 확인하십시오.` | signal cycle 예외 | `_auto_trade_run_signal_cycle` | `gui_auto_trade_timer.py:305-309` | Queue/오류·경고/한글 |
| 145 | `마감·청산 Command 처리: 진행 {processed} / 차단 {blocked}` | close command timer activity | `auto_trade_run_operation_cycle` | `gui_auto_trade_timer.py:487-490` | 운영 시작·정지/개발·디버그/한글·혼합/동적 |
| 146 | `ATS 청산 Command 처리: 진행 {processed} / 실패 {failed}` | ATS command timer activity | 같은 함수 | `gui_auto_trade_timer.py:496-499` | 운영 시작·정지/개발·디버그/한글·혼합/동적 |
| 147 | `시간정책 자동반영: 변경 {changed}개[ / 실패 {failed}개]` | time policy status mutation | `auto_trade_on_time_policy_timer_tick` | `gui_auto_trade_timer.py:666-669` | 운영 시작·정지/개발·디버그/한글/동적 |
| 148 | `환경설정 저장 완료` | 환경설정 dialog 저장 | `AutoTradeSettingWindow._handle_operation_environment_settings_saved` | `gui_auto_trade_setting_window.py:12743-12755` | 예산·설정/한글 |
| 149 | `분봉조회할 종목 1개를 선택하세요.` | selection count 오류 | `AutoTradeSettingWindow.fetch_minute_candles_for_selected_stock` | `gui_auto_trade_setting_window.py:10402` | 기타/오류·경고/한글 |
| 150 | `키움 API가 초기화되지 않았습니다.` | API object 없음 | 같은 함수 | `gui_auto_trade_setting_window.py:10409` | 연결/오류·경고/한글·혼합 |
| 151 | `키움 API 사용불가: {reason}` | API unavailable reason. 예: `키움 API 사용불가: control unavailable` | 같은 함수 | `gui_auto_trade_setting_window.py:10414` | 연결/오류·경고/한글·혼합/동적 |
| 152 | `키움 로그인 후 분봉조회가 가능합니다.` | API disconnected | 같은 함수 | `gui_auto_trade_setting_window.py:10418` | 로그인/연결/오류·경고/한글 |
| 153 | `{code} {name} candles.json 저장 완료: {saved_count}개[ ({warning_or_more_pages})]` | callback 성공. 예: `005930 삼성전자 candles.json 저장 완료: 300개 (additional pages available)` | callback `handle_result` | `gui_auto_trade_setting_window.py:10422-10428` | 기타/개발·디버그/한글·혼합/동적 |
| 154 | `{code} {name} 분봉조회 요청됨` | request accepted. 예: `005930 삼성전자 분봉조회 요청됨` | `fetch_minute_candles_for_selected_stock` | `gui_auto_trade_setting_window.py:10447` | 기타/한글/동적 |
| 155 | `{code} {name} 분봉조회 실패: {message_or_exception}` | callback/request 실패. 예: `005930 삼성전자 분봉조회 실패: timeout` | 같은 함수와 callback | `gui_auto_trade_setting_window.py:10432,10443,10450` | 기타/오류·경고/한글/동적 |
| 156 | `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}` | 수동 dry-run. 예: `주문후보검증: 확인 5 / 차단 1 / 허용 4 / 오류 0` | `AutoTradeSettingWindow.preview_order_candidates_for_pending_signals` | `gui_auto_trade_setting_window.py:10452-10464` | Queue/개발·디버그/한글/동적 |
| 157 | `주문후보검증 실패: {exception}` | dry-run 예외. 예: `주문후보검증 실패: invalid queue` | 같은 함수 | `gui_auto_trade_setting_window.py:10466` | Queue/오류·경고/개발·디버그/한글/동적 |

### REAL_READY / Execution Preview / Queue / SendOrder

| 번호 | 실제 문자열 또는 Template | 발생 조건과 동적 값 / 대표 예시 | Caller 함수 | 파일 | 분류 |
|---:|---|---|---|---|---|
| 158 | `수동 실주문 후보 활성화: order_id를 입력하세요.` | candidate id 없음 | `AutoTradeSettingWindow.enable_execution_candidate_manually` | `gui_auto_trade_setting_window.py:10634` | REAL_READY/개발·디버그/한글·혼합 |
| 159 | `수동 실주문 후보 활성화 차단` | activation guard 차단. 동일 원문 3개 branch | 같은 함수 | `gui_auto_trade_setting_window.py:10651,10674,10694` | REAL_READY/오류·경고/개발·디버그/한글 |
| 160 | `수동 실주문 후보 활성화 취소` | 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:10678` | REAL_READY/개발·디버그/한글 |
| 161 | `수동 실주문 후보 활성화 {status_text}` | status=`완료/차단`. 예: `수동 실주문 후보 활성화 완료` | 같은 함수 | `gui_auto_trade_setting_window.py:10704-10705` | REAL_READY/개발·디버그/한글/동적 |
| 162 | `REAL_READY 수동 점검: order_id를 입력하세요.` | preflight id 없음 | `AutoTradeSettingWindow.run_real_ready_preflight_manually` | `gui_auto_trade_setting_window.py:10919` | REAL_READY/개발·디버그/한글·혼합 |
| 163 | `REAL_READY 수동 점검 차단` | Korean preflight guard 차단. 동일 원문 4개 branch | 같은 함수 | `gui_auto_trade_setting_window.py:10937,10956,11001,11018` | REAL_READY/오류·경고/개발·디버그/한글·혼합 |
| 164 | `REAL_READY manual preflight cancelled` | 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:10961` | REAL_READY/영문/개발·디버그 |
| 165 | `REAL_READY manual preflight blocked` | runtime preflight 차단 | 같은 함수 | `gui_auto_trade_setting_window.py:10978` | REAL_READY/오류·경고/영문/개발·디버그 |
| 166 | `REAL_READY 수동 점검 {status_text}` | status=`완료/차단`. 예: `REAL_READY 수동 점검 완료` | 같은 함수 | `gui_auto_trade_setting_window.py:11029-11030` | REAL_READY/개발·디버그/한글·혼합/동적 |
| 167 | `Execution Preview: order_id를 입력하세요.` | preview id 없음 | `AutoTradeSettingWindow.preview_execution_for_real_ready_order_manual` | `gui_auto_trade_setting_window.py:11229` | Execution Preview/영문/개발·디버그 |
| 168 | `Execution Preview blocked: real trade guard is not ready` | real trade guard 미준비 | 같은 함수 | `gui_auto_trade_setting_window.py:11239` | Execution Preview/오류·경고/영문/개발·디버그 |
| 169 | `Execution Preview cancelled before runtime commit confirmation` | runtime commit 전에 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:11253` | Execution Preview/영문/개발·디버그 |
| 170 | `Execution Preview {status_text}: {order_id}` | status=`통과/차단`. 예: `Execution Preview 통과: ORDER-1` | 같은 함수 | `gui_auto_trade_setting_window.py:11314-11315` | Execution Preview/개발·디버그/한글·혼합/동적 |
| 171 | `Execution Preview 실패: {exception}` | preview 예외. 예: `Execution Preview 실패: stale runtime` | 같은 함수 | `gui_auto_trade_setting_window.py:11317` | Execution Preview/오류·경고/개발·디버그/한글·혼합/동적 |
| 172 | `수동 Queue 저장: 먼저 유효한 Execution Preview를 실행하세요.` | valid preview 없음 | `AutoTradeSettingWindow.commit_last_execution_preview_queue_manually` | `gui_auto_trade_setting_window.py:11563` | Queue/Execution Preview/오류·경고/개발·디버그/한글·혼합 |
| 173 | `수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.` | preview/queue snapshot stale 또는 invalid. 동일 원문 2개 branch | 같은 함수 | `gui_auto_trade_setting_window.py:11582,11597` | Queue/Execution Preview/오류·경고/개발·디버그/한글·혼합 |
| 174 | `Manual Queue commit blocked: runtime commit result is required` | runtime commit evidence 없음 | 같은 함수 | `gui_auto_trade_setting_window.py:11613` | Queue/오류·경고/영문/개발·디버그 |
| 175 | `수동 Queue 저장: 취소됨` | 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:11617` | Queue/개발·디버그/한글·혼합 |
| 176 | `Manual Queue commit blocked: readiness policy failed` | readiness policy 차단 | 같은 함수 | `gui_auto_trade_setting_window.py:11642` | Queue/오류·경고/영문/개발·디버그 |
| 177 | `수동 Queue 저장 {status_text}` | status=`완료/차단`. 예: `수동 Queue 저장 완료` | 같은 함수 | `gui_auto_trade_setting_window.py:11675-11676` | Queue/개발·디버그/한글·혼합/동적 |
| 178 | `Manual Cancel: source order id is required` | cancel source id 없음 | `AutoTradeSettingWindow.cancel_pending_order_manually` | `gui_auto_trade_setting_window.py:12049` | Queue/SendOrder/영문/개발·디버그 |
| 179 | `Manual Cancel cancelled` | manual cancel 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:12093` | Queue/SendOrder/영문/개발·디버그 |
| 180 | `Manual Modify: source order id is required` | modify source id 없음 | `AutoTradeSettingWindow.modify_pending_order_manually` | `gui_auto_trade_setting_window.py:12133` | Queue/SendOrder/영문/개발·디버그 |
| 181 | `Manual Modify cancelled` | manual modify 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:12203` | Queue/SendOrder/영문/개발·디버그 |
| 182 | `Manual SendOrder: ORDER_QUEUED record id is required` | queued record id 없음 | `AutoTradeSettingWindow.send_order_for_order_queued_manually` | `gui_auto_trade_setting_window.py:12246` | Queue/SendOrder/영문/개발·디버그 |
| 183 | `Manual SendOrder blocked` | read/status/environment/snapshot/final gate/claim 등 12개 branch 차단 | 같은 함수 | `gui_auto_trade_setting_window.py:12264-12488` | Queue/SendOrder/오류·경고/영문/개발·디버그 |
| 184 | `Manual SendOrder cancelled` | 사용자 취소 | 같은 함수 | `gui_auto_trade_setting_window.py:12320` | Queue/SendOrder/영문/개발·디버그 |
| 185 | `Manual SendOrder {status_text}` | status=`completed/blocked`. 예: `Manual SendOrder completed` | 같은 함수 | `gui_auto_trade_setting_window.py:12511-12512` | Queue/SendOrder/영문/개발·디버그/동적 |

## Production에서 제외한 항목

### Dead Code 후보

다음 4개 문구는 source function에는 존재하지만 현재 Production Python caller가 없다.
정의 위치만 있고 import/call site가 없어 Production 총 185종에서 제외했다.

| 원문/Template | 정의 함수 | 파일 |
|---|---|---|
| `신호평가 전용 전환 대상 없음` | `start_signal_probe_only_for_selected_stocks` | `gui_auto_trade_run_control.py:1959-1965` |
| `신호평가 전용 시작: {started}개[ / 실패 {failed}개]` | 같은 함수 | `gui_auto_trade_run_control.py:2001-2008` |
| `신호평가 전용 중지 대상 없음` | `stop_signal_probe_only_for_selected_stocks` | `gui_auto_trade_run_control.py:2013-2019` |
| `신호평가 전용 중지: {stopped}개[ / 실패 {failed}개]` | 같은 함수 | `gui_auto_trade_run_control.py:2051-2058` |

### 별도 UI / 테스트 / 비상태메시지

- `UI 배치 확인용 시안 · 실제 데이터 및 주문 기능과 연결되지 않음`: `gui_main_monitoring_preview.py`의 독립 preview status bar. 메인 QLabel 미도달.
- 테스트가 `showMessage("login succeeded")`를 직접 호출하는 경로: 테스트 writer이며 Production 종류 수에는 별도 가산하지 않음. 원문 자체는 Production ID 9에도 존재.
- QMessageBox/Toast 전용 문자열, logger 문자열, production event journal template: 메인 footer writer가 아니므로 제외.

## 추가 분석

### 1. 영문 Production 메시지 21종

ID `9-14`, `164-165`, `167-169`, `174`, `176`, `178-185`.

### 2. 한글/영문 의미 중복 및 동일 의미 다문구 9그룹

1. 로그인 성공: ID 5 `로그인 상태: 연결됨` / ID 9 `login succeeded`
2. 로그인 실패·연결해제: ID 8, 10-14, 16
3. Recovery 미완료·불일치: ID 19, 21-43
4. 검토관리로 인한 Operation Start 차단: ID 28, 32, 74, 93, 98
5. 시작예산/필수설정 미완료: ID 76-78, 95-97
6. Runtime/state 오류로 인한 Start 차단: ID 79-80, 90, 104-106, 109, 113
7. 이미 운영 중: ID 75, 94, 108의 `운영중 유지`, 114
8. 조기마감 취소·대상 없음: ID 54-55, 132-136
9. 주문후보검증: timer 상세형 ID 141 / 수동 dry-run형 ID 156-157

### 3. 개발/내부용 노출 후보 46종

엄격하게 raw 영문, 내부 오류코드/파일명, timer 계측, 수동 실행 단계명을 직접 노출하는
항목만 후보로 잡았다.

- raw login: ID `9-14` (6종)
- ATS raw token: ID `123,125` (2종)
- timer/command 계측: ID `141-147` (7종)
- 저장 파일명 노출: ID `153` (1종)
- 수동 주문후보 dry-run: ID `156-157` (2종)
- 수동 execution diagnostic: ID `158-185` (28종)

### 4. 내부 실행 단계명이 그대로 노출되는 메시지 29종

- `INVALID_ATS_EXECUTION_METHOD`: ID `123,125`
- `SendOrder`: ID `128`
- `candles.json`: ID `153`
- `order_id`: ID `158`
- `REAL_READY`, `Execution Preview`, `Queue`, `Manual Cancel/Modify/SendOrder`: ID `162-185`

이 절은 분류만 수행했으며 문구 변경/삭제 의견을 코드에 반영하지 않았다.

## 최종 수치

- Production 하단 메시지 총 **185종**
- 동적 Template **78종**
- 영문 **21종**
- 한글 또는 한글/영문 혼합 **164종**
- 의미 중복 **9그룹**
- 개발/내부용 노출 후보 **46종**
- Production caller 없는 Dead Code 후보 **4종**

## 운영자용 Footer 최종 정규화

- 적용 경계: `QStatusBar.messageChanged` -> `MainWindow._project_main_status_message()` -> `main_status_message_label`
- 허용 canonical message: **30종**
- 아이콘별: `✓` 8종 / `✕` 11종 / `▷` 7종 / `●` 3종 / `※` 1종
- 기존 185종 disposition: `CONVERTED` **19종** / `MERGED` **112종** / `FOOTER_REMOVED` **54종**
- `FOOTER_REMOVED`는 footer 투영만 생략한다. 기존 Caller, Event Journal, logger, Runtime/Execution evidence는 변경하지 않았다.
- 숫자 결과가 포함된 동적 문구는 실제 치환된 count/action을 기준으로 성공 또는 실패 canonical message를 선택한다.
- `CONVERTED`는 각 canonical 의미의 대표 원문, `MERGED`는 같은 canonical 의미로 합쳐지는 후속 원문이다.

| ID | 기존 메시지 | 최종 처리 | 운영자 메시지 |
|---:|---|---|---|
| 1 | `준비 완료` | `CONVERTED` | `✓ 준비 완료` |
| 2 | `운영 재개 승인이 취소되었습니다.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 3 | `운영 재개 승인 완료: {status}` | `CONVERTED` | `✓ 서버 인증 완료` |
| 4 | `키움 OpenAPI를 사용할 수 없습니다. 설치 상태와 32비트 실행 환경을 확인하십시오.` | `CONVERTED` | `✕ 서버 연결 실패` |
| 5 | `로그인 상태: 연결됨` | `CONVERTED` | `✓ 서버 연결 완료` |
| 6 | `키움 로그인 요청 중 오류가 발생했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 서버 연결 실패` |
| 7 | `로그인 요청됨` | `CONVERTED` | `▷ 로그인 중` |
| 8 | `키움 로그인 요청을 완료하지 못했습니다. 키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 서버 연결 실패` |
| 9 | `login succeeded` | `MERGED` | `✓ 서버 연결 완료` |
| 10 | `user info exchange failed` | `CONVERTED` | `✕ 사용자 정보 확인 실패` |
| 11 | `server connection failed` | `MERGED` | `✕ 서버 연결 실패` |
| 12 | `version processing failed` | `CONVERTED` | `✕ 버전 확인 실패` |
| 13 | `login failed: {code}` | `CONVERTED` | `✕ 로그인 실패 ({code})` |
| 14 | `kiwoom api disconnected` | `CONVERTED` | `✕ 서버 연결 끊김` |
| 15 | `미연결 상태` | `MERGED` | `✕ 서버 연결 끊김` |
| 16 | `로그인 상태: 실패` | `MERGED` | `✕ 서버 연결 끊김` |
| 17 | `계좌비밀번호 입력 기능을 사용할 수 없습니다.` | `CONVERTED` | `✕ 서버 인증 실패` |
| 18 | `계좌비밀번호 입력창을 열지 못했습니다.` | `MERGED` | `✕ 서버 인증 실패` |
| 19 | `{action}할 수 없습니다. 로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오.[\n\n원인: {detail}]` | `CONVERTED` | `✕ 작업 처리 실패` |
| 20 | `서버 연결 및 계좌 복구 확인 후 사용할 수 있습니다.` | `MERGED` | `✕ 서버 연결 실패` |
| 21 | `키움 서버에 로그인되어 있지 않습니다.` | `MERGED` | `✕ 서버 연결 실패` |
| 22 | `로그인 세션 정보를 확인할 수 없습니다. 키움 서버에 다시 로그인하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 23 | `운영할 계좌를 선택하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 24 | `Recovery 데이터를 읽을 수 없습니다. 복구를 다시 실행한 후 운영을 시작하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 25 | `운영 시작에 필요한 Recovery 정보를 확인할 수 없습니다. 로그인과 계좌 선택 상태를 확인한 후 Recovery를 다시 실행하십시오.` | `CONVERTED` | `✕ 운영 시작 실패` |
| 26 | `운영 시작 전에 Recovery가 완료되지 않았습니다. 로그인과 계좌 선택 후 Recovery를 완료하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 27 | `Recovery가 진행 중입니다. 복구가 완료된 후 다시 시도하십시오.` | `CONVERTED` | `▷ 서버 인증 중` |
| 28 | `복구가 필요한 종목이 남아 있습니다. 검토관리에서 해당 종목을 처리하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 29 | `현재 로그인 또는 계좌와 Recovery 정보가 일치하지 않습니다. Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 30 | `이전 Recovery 정보는 현재 세션에서 사용할 수 없습니다. Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 31 | `선택한 종목의 Recovery가 아직 완료되지 않았습니다.` | `MERGED` | `▷ 서버 인증 중` |
| 32 | `선택한 종목은 복구 검토 대상입니다. 검토관리에서 해당 종목을 처리하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 33 | `선택한 종목의 Recovery에 실패했습니다. 검토관리에서 상태를 확인하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 34 | `Runtime 데이터를 읽을 수 없어 Recovery에 실패했습니다. 검토관리에서 Runtime 상태를 확인하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 35 | `계좌의 보유 또는 미체결 정보를 확인하지 못했습니다. 키움 연결 상태를 확인한 후 Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 36 | `운영 주기 실행을 시작하지 못했습니다. 로그를 확인한 후 Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 37 | `Recovery가 완료된 운영 대상 종목이 없습니다. 검토관리에서 종목 상태를 확인하십시오.` | `CONVERTED` | `※ 운영 대상 없음` |
| 38 | `계좌 Recovery에 실패했습니다. 로그인과 계좌 상태를 확인한 후 Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 39 | `{action} 불가\n\n사용할 계좌 정보가 아직 확인되지 않았습니다.\n로그인과 계좌 선택 상태를 확인해 주세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 40 | `{action} 불가\n\n프로그램 시작 후 기존 운영 상태를 확인하고 있습니다.\n확인이 끝난 뒤 다시 시도해 주세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 41 | `{action} 불가\n\n이전 운영 상태를 확인하지 못했습니다.\n운영 상태와 로그를 확인해 주세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 42 | `{action} 불가\n\n확인이 필요한 운영 항목이 남아 있습니다.\n검토관리에서 상태를 확인해 주세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 43 | `{action} 불가\n\n프로그램 시작 후 운영 상태 확인이 아직 완료되지 않았습니다.\n잠시 후 다시 시도해 주세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 44 | `선택한 루틴 정보를 읽을 수 없습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 45 | `선택한 루틴에 등록된 종목이 없습니다.\n자동매매 설정에서 종목을 등록하십시오.` | `MERGED` | `※ 운영 대상 없음` |
| 46 | `{instance} {requested_action} 완료 (대상 {count}종목)` | `MERGED` | `✓ 운영 시작 / ✓ 운영 정지 (실제 action 기준)` |
| 47 | `{instance} {requested_action} 실패: {user_message}` | `MERGED` | `✕ 작업 처리 실패` |
| 48 | `{scope} {command}: {display_name} / 대상 0` | `MERGED` | `※ 운영 대상 없음` |
| 49 | `{scope} {command} 취소: {display_name}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 50 | `{scope} {command}: {display_name} / 성공 {applied} / 차단 {failed}` | `MERGED` | `✓ 설정 저장 완료 / ✕ 작업 처리 실패 (성공·차단 수 기준)` |
| 51 | `루틴 {command}: {display_name} / 대상 0` | `MERGED` | `※ 운영 대상 없음` |
| 52 | `루틴 {command} 취소: {display_name}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 53 | `루틴 {command}: {display_name} / 성공 {applied} / 차단 {failed}` | `MERGED` | `✓ 설정 저장 완료 / ✕ 작업 처리 실패 (성공·차단 수 기준)` |
| 54 | `관제창 조기마감: 대상 0` | `MERGED` | `※ 운영 대상 없음` |
| 55 | `관제창 조기마감 취소` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 56 | `관제창 조기마감: 성공 {applied} / 차단 {failed}` | `MERGED` | `✓ 설정 저장 완료 / ✕ 작업 처리 실패 (성공·차단 수 기준)` |
| 57 | `거래권한을 변경할 종목을 1개 이상 선택하세요.` | `MERGED` | `✕ 작업 처리 실패` |
| 58 | `거래권한 변경: {changed}개[ / 차단 {blocked}개]` | `MERGED` | `✓ 설정 저장 완료 / ✕ 작업 처리 실패 (변경·차단 수 기준)` |
| 59 | `운영 중에는 더블클릭으로 운영 대상을 변경할 수 없습니다. 우클릭 운영시작을 사용하세요.` | `MERGED` | `✕ 운영 시작 실패` |
| 60 | `{code} {name} {label}` | `MERGED` | `✓ 설정 저장 완료 (실제 label 기준)` |
| 61 | `현재 루틴 전체 종목 선택: {row_count}개` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 62 | `현재 루틴 종목 선택 해제` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 63 | `루틴 등록해제 완료: {count}개` | `CONVERTED` | `✓ 설정 저장 완료` |
| 64 | `{label} 전환 완료: {count}개` | `MERGED` | `✓ 설정 저장 완료` |
| 65 | `수동운영 기본 리셋 완료: {count}개` | `MERGED` | `✓ 설정 저장 완료` |
| 66 | `오늘 운영이 종료되었습니다.` | `CONVERTED` | `✓ 운영 정지` |
| 67 | `운영시작 대상이 없습니다. 운영 제외를 해제한 뒤 다시 시도하세요.` | `MERGED` | `※ 운영 대상 없음` |
| 68 | `운영 상태를 변경하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 69 | `전역 운영 상태 기록에 실패했습니다. 로그를 확인하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 70 | `운영 시작 {started}개[ · 검토 제외 {review}개][ · 설정 제외 {validation}개][ · 실패 {failed}개]` | `MERGED` | `✓ 운영 시작 / ✕ 운영 시작 실패 (started 수 기준)` |
| 71 | `대상종목 {requested}  \|  기운영중 {already}  \|  운영시작 {started}  \|  운영불가 {unavailable}[\n{reason_label} {count} · ...]` | `MERGED` | `✓ 운영 시작 / ✕ 운영 시작 실패 (started·already·unavailable 수 기준)` |
| 72 | `{name_or_code} 운영을 시작했습니다.` | `CONVERTED` | `✓ 운영 시작` |
| 73 | `{stock}은/는 긴급정지 상태입니다.` | `CONVERTED` | `✕ 긴급정지` |
| 74 | `{stock}은/는 검토관리 대상입니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 75 | `{stock}은/는 이미 운영 중입니다.` | `MERGED` | `✓ 운영 시작` |
| 76 | `{stock}의 필수 운영 설정이 완료되지 않았습니다.\n자동매매 설정을 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 77 | `{stock}의 현재 세션 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 78 | `{stock}의 초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 79 | `{stock}의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 80 | `{stock}의 운영 상태를 저장하거나 다시 확인하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 81 | `{stock}의 Recovery가 아직 완료되지 않았습니다.\n복구가 완료된 뒤 다시 시도하십시오.` | `MERGED` | `▷ 서버 인증 중` |
| 82 | `{stock}의 Recovery에 실패했습니다.\n검토관리에서 상태를 확인하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 83 | `오늘의 정상 운영이 이미 종료되었습니다.\n다음 거래일에 운영을 시작하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 84 | `{stock}은/는 시간운영 종료로 운영을 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 85 | `{stock}은/는 현재 운영시작 가능 시간이 아닙니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 86 | `{stock}은/는 보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 87 | `{stock}은/는 미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 88 | `{stock}은/는 취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 89 | `{stock}은/는 마감 또는 청산 절차가 진행 중입니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 90 | `{stock}의 운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 91 | `{stock}은/는 현재 운영을 시작할 수 없는 상태입니다.\n자동매매 설정과 검토관리 상태를 확인하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 92 | `모든 종목이 긴급정지 상태입니다.` | `MERGED` | `✕ 긴급정지` |
| 93 | `모든 등록 종목이 검토 대상으로 분리되어 있습니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 94 | `선택한 루틴은 이미 운영 중입니다.` | `MERGED` | `✓ 운영 시작` |
| 95 | `현재 세션의 가격 정보를 아직 확인하지 못해 시작금액을 확정할 수 없습니다.\n시세 정보를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 96 | `초회 매수 주수가 설정되지 않았습니다.\n자동매매 설정에서 1주 이상으로 설정하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 97 | `모든 등록 종목의 필수 설정이 완료되지 않았습니다.\n자동매매 설정을 확인하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 98 | `모든 등록 종목이 검토 대상으로 분리되었습니다.\n검토관리에서 처리한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 99 | `현재는 매매 운영 시간이 아닙니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 100 | `보유수량이 남아 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 101 | `미체결 주문이 남아 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 102 | `취소 처리 중인 주문이 있어 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 103 | `마감 또는 청산 절차가 진행 중이어서 운영을 다시 시작할 수 없습니다.` | `MERGED` | `✕ 운영 시작 실패` |
| 104 | `종목의 운영 상태 데이터를 읽을 수 없습니다.\n검토관리에서 Runtime 상태를 확인하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 105 | `종목의 운영 상태를 저장하지 못했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 106 | `운영 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 107 | `현재 운영을 시작할 수 있는 종목이 없습니다.\n검토관리와 자동매매 설정을 확인하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 108 | `[운영중 유지: {already}종목\n]{reason_label}: {count}종목[\n- {stock_label}...]` | `MERGED` | `✓ 운영 시작 / ✕ 운영 시작 실패 (resolved reason group 기준)` |
| 109 | `운영 시작 대상을 확인하는 중 오류가 발생했습니다.\n화면을 새로고침한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 110 | `운영 시작 전 서버와 계좌 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 다시 시도하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 111 | `전역 긴급정지 상태입니다. 정지해제 후 운영시작을 다시 시도하십시오.` | `MERGED` | `✕ 긴급정지` |
| 112 | `운영을 시작할 종목을 1개 이상 선택하십시오.` | `MERGED` | `✕ 운영 시작 실패` |
| 113 | `복구 상태를 확인하는 중 오류가 발생했습니다.\n로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | `MERGED` | `✕ 서버 인증 실패` |
| 114 | `선택한 종목이 모두 이미 운영 중입니다.` | `MERGED` | `✓ 운영 시작` |
| 115 | `전역 긴급정지 기록에 실패했습니다. 종목별 긴급정지를 시작하지 않았습니다.` | `MERGED` | `✕ 긴급정지` |
| 116 | `긴급정지 실행 완료: {changed}개 종목[ / 기존 검토 유지 {preserved}개][ / 실패 {failed}개 / 전역 차단 유지]` | `MERGED` | `✕ 긴급정지` |
| 117 | `종목 검토정지: 변경 {changed}개[ / 이미 검토관리 {skipped}개][ / 실패 {failed}개]` | `MERGED` | `✓ 운영 정지 / ✕ 운영 정지 실패 (변경·실패 수 기준)` |
| 118 | `전체 긴급정지 상태에서는 상단 정지해제를 사용하십시오.` | `MERGED` | `✕ 긴급정지` |
| 119 | `종목 정지해제: 정상 {normal}개 / 검토관리 유지 {review}개[ / 긴급정지 유지 {blocked}개][ / 대상아님 {skipped}개][ / 실패 {failed}개]` | `CONVERTED` | `✓ 긴급정지 해제` |
| 120 | `전체 정지해제 차단 \| {operator_reason}` | `MERGED` | `✕ 작업 처리 실패` |
| 121 | `정지해제 완료: 정상 {normal}개 / 검토관리 {review}개` | `MERGED` | `✓ 긴급정지 해제` |
| 122 | `정지해제 미완료: 정상 {normal}개 / 검토관리 {review}개 / 긴급정지 잔존 {remaining}개 / 실패 {failed}개 / 전역 차단 유지` | `MERGED` | `✕ 작업 처리 실패` |
| 123 | `ATS 주문방식 설정 오류: INVALID_ATS_EXECUTION_METHOD / {codes}` | `MERGED` | `✕ 작업 처리 실패` |
| 124 | `ATS설정 변경 완료: {label} {ON_or_OFF} / {changed}개` | `MERGED` | `✓ 설정 저장 완료` |
| 125 | `ATS 주문방식 저장 실패: INVALID_ATS_EXECUTION_METHOD` | `MERGED` | `✕ 작업 처리 실패` |
| 126 | `ATS 주문방식 변경 완료: {method_label} / {succeeded}개` | `MERGED` | `✓ 설정 저장 완료` |
| 127 | `ATS {method}매도 취소` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 128 | `ATS {method}매도 SendOrder 접수 기록: {count}개` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 129 | `ATS {method}매도 취소 확인 대기: {count}개` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 130 | `ATS {method}매도 청산 대상 없음: {count}개` | `MERGED` | `※ 운영 대상 없음` |
| 131 | `개별청산 설정 완료: {minutes}분/{method} / 대상 {count}개` | `MERGED` | `✓ 설정 저장 완료` |
| 132 | `현재 상태는 마감정책 취소 대상이 아닙니다.` | `MERGED` | `✕ 작업 처리 실패` |
| 133 | `마감정책이 취소되었습니다.` | `MERGED` | `✓ 설정 저장 완료` |
| 134 | `조기마감 불가: 청산 진행 중` | `MERGED` | `✕ 작업 처리 실패` |
| 135 | `조기마감 적용: 0개` | `MERGED` | `※ 운영 대상 없음` |
| 136 | `조기마감 취소` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 137 | `조기마감 적용: {completed}개[ / 제외 {skipped}개]` | `MERGED` | `✓ 설정 저장 완료` |
| 138 | `Runtime 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 139 | `시간정책 상태를 갱신하지 못했습니다. 로그를 확인한 뒤 Recovery를 다시 실행하십시오.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 140 | `자동매매 운영 주기를 처리하지 못했습니다. 로그를 확인하십시오.` | `MERGED` | `✕ 작업 처리 실패` |
| 141 | `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors} / 후보 {created} / 승인검사 {approval_checked} / 승인 {approved}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 142 | `실자동매매 주문처리: 실행 {processed} / 차단 {blocked}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 143 | `루틴 신호 로그: 기록 {logged}개[ / 오류 {errors}개]` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 144 | `주문 후보를 검증하는 중 오류가 발생했습니다. 로그를 확인하십시오.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 145 | `마감·청산 Command 처리: 진행 {processed} / 차단 {blocked}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 146 | `ATS 청산 Command 처리: 진행 {processed} / 실패 {failed}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 147 | `시간정책 자동반영: 변경 {changed}개[ / 실패 {failed}개]` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 148 | `환경설정 저장 완료` | `MERGED` | `✓ 설정 저장 완료` |
| 149 | `분봉조회할 종목 1개를 선택하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 150 | `키움 API가 초기화되지 않았습니다.` | `MERGED` | `✕ 서버 연결 실패` |
| 151 | `키움 API 사용불가: {reason}` | `MERGED` | `✕ 서버 연결 실패` |
| 152 | `키움 로그인 후 분봉조회가 가능합니다.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 153 | `{code} {name} candles.json 저장 완료: {saved_count}개[ ({warning_or_more_pages})]` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 154 | `{code} {name} 분봉조회 요청됨` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 155 | `{code} {name} 분봉조회 실패: {message_or_exception}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 156 | `주문후보검증: 확인 {checked} / 차단 {blocked} / 허용 {allowed} / 오류 {errors}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 157 | `주문후보검증 실패: {exception}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 158 | `수동 실주문 후보 활성화: order_id를 입력하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 159 | `수동 실주문 후보 활성화 차단` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 160 | `수동 실주문 후보 활성화 취소` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 161 | `수동 실주문 후보 활성화 {status_text}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 162 | `REAL_READY 수동 점검: order_id를 입력하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 163 | `REAL_READY 수동 점검 차단` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 164 | `REAL_READY manual preflight cancelled` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 165 | `REAL_READY manual preflight blocked` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 166 | `REAL_READY 수동 점검 {status_text}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 167 | `Execution Preview: order_id를 입력하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 168 | `Execution Preview blocked: real trade guard is not ready` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 169 | `Execution Preview cancelled before runtime commit confirmation` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 170 | `Execution Preview {status_text}: {order_id}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 171 | `Execution Preview 실패: {exception}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 172 | `수동 Queue 저장: 먼저 유효한 Execution Preview를 실행하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 173 | `수동 Queue 저장 차단: Execution Preview를 다시 실행하세요.` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 174 | `Manual Queue commit blocked: runtime commit result is required` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 175 | `수동 Queue 저장: 취소됨` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 176 | `Manual Queue commit blocked: readiness policy failed` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 177 | `수동 Queue 저장 {status_text}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 178 | `Manual Cancel: source order id is required` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 179 | `Manual Cancel cancelled` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 180 | `Manual Modify: source order id is required` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 181 | `Manual Modify cancelled` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 182 | `Manual SendOrder: ORDER_QUEUED record id is required` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 183 | `Manual SendOrder blocked` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 184 | `Manual SendOrder cancelled` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |
| 185 | `Manual SendOrder {status_text}` | `FOOTER_REMOVED` | `하단 미표시 (기존 Event/Log 경로 유지)` |

### 정규화 결과

- raw 영문 Production footer 잔존: **0종**
- `REAL_READY`, `Execution Preview`, `Manual Queue`, `Manual SendOrder`, `ORDER_QUEUED`, `Dispatch`, `Runtime Commit` footer 노출: **0종**
- Event/Log evidence: **보존**
