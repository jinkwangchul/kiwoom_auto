# Reference Edition - Stock Registration Contract

생성일: 2026-08-26
Milestone: Stock Registration Original Contract Restoration

이 문서는 종목등록 기능과 환경설정 `8. 종목등록 설정`의 등록위치 정책을
검색/참조용으로 고정한다. 종목등록은 운영시작과 별도 기능이며, 프로그램
전체 운영 상태를 이유로 신규 종목등록을 차단하지 않는다.

## 1. Stock Registration During Operation

프로그램 전체가 운영중이어도 신규 종목등록은 허용한다.
검색 기준 문구: 운영중에도 종목등록 가능.

다음 값이나 상태는 registration blocker가 아니다.

- global RUNNING
- current running participant 존재
- current-session participant 존재
- `running_registered_operation_targets`
- 하단 `▶ 운영중` 표시

`현재 운영 중에는 종목을 등록할 수 없습니다.` 형태의 전역 차단은 현재
계약과 충돌한다. 향후 AI/개발자는 `운영중 = 구조변경 전면금지`로 확대해석해
종목등록을 막는 Production guard를 도입하지 않는다.

## 2. Environment Registration Location Source

신규 종목의 등록 위치는 `operation_policy.json`의
`stock_registration.default_location`을 따른다.

허용 내부 값은 다음 두 가지다.

- `WAITING`
- `EXCLUDED`

환경설정 UI의 `8. 종목등록 설정 -> 등록위치` 표시는 다음처럼 매핑한다.

- `대기` -> `WAITING`
- `제외` -> `EXCLUDED`

Production consumer는 `gui_stock_data.append_base_stock()`에서
`apply_registration_location_for_new_stock()`로 이어지는 기존 경로다.
새 Source of Truth, 새 registration lifecycle, 별도 deferred-registration queue를
만들지 않는다.

## 3. Registration Is Not Operation Start

종목등록과 운영시작은 별도 기능이다.
검색 기준 문구: 등록과 운영시작 분리.

신규 등록 종목은 등록만으로 다음 상태가 되지 않는다.

- current-session participant
- execution universe member
- execution-ready target
- 자동 주문 대상

실제 운영 참가는 기존 별도 `운영시작` 계약을 따른다. 등록 직후 current
operation participant set이나 execution universe를 재구성하지 않는다.

## 4. Main Counter Buckets

관제창 상단의 `운영 / 대기 / 제외 / 검토`는 서로 배타적인 전역 상태가 아니라
종목별 집계 bucket이다.

따라서 프로그램이 운영중이어도 대기/제외 종목이 함께 존재할 수 있다.
예를 들어 `운영 2 / 대기 3 / 제외 1 / 검토 0`은 정상 상태다.

global RUNNING 또는 current-session participant 존재를 이유로 대기/제외 신규
등록을 금지하면 안 된다.
검색 기준 문구: global RUNNING registration blocker 금지.

## 5. Existing Registration Safety Guards

이 계약은 모든 등록을 무조건 허용한다는 뜻이 아니다. 기존 registration safety
guard는 유지한다.

- 중복 등록 금지
- invalid stock 차단
- stock identity 검증 실패 차단
- registration validation 실패 차단
- 기존 writer safety 유지

환경설정 `stock_registration.default_location`은 신규 등록 시 적용되는 정책이다.
설정 변경만으로 기존 등록 종목을 `대기`와 `제외` 사이에서 일괄 이동시키는
정책으로 해석하지 않는다. 기존 종목 mutation은 별도 기능 계약을 따른다.
