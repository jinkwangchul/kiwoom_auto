# 작업재개요약 - Execution Preview Pipeline 1차

작성일: 2026-07-03

## 현재 상태

Execution Preview 계층 1차 구현과 회귀 검증이 완료된 상태다.

핵심 원칙은 다음과 같다.

- 실제 주문 실행은 하지 않는다.
- SendOrder는 호출하지 않는다.
- ORDER_QUEUED는 생성하지 않는다.
- order_queue.json은 수정하지 않는다.
- runtime/order_executions.json은 생성/수정하지 않는다.
- runtime/order_locks.json은 생성/수정하지 않는다.
- execution_enabled는 변경하지 않는다.
- rules.json은 수정하지 않는다.
- Kiwoom OpenAPI 코드값 직접 매핑은 하지 않는다.
- 모든 결과는 메모리 dict preview로만 반환한다.

## 추가된 Preview 계층

### 1. Hoga Mapper

파일: order_hoga_mapper.py

함수: map_order_hoga_preview(order)

역할:
- order_intent의 호가 의도를 내부 표준 hoga 값으로 preview 변환한다.
- 시장가 -> MARKET
- 현재가/지정가 -> LIMIT
- 미확정/빈값/알 수 없음/지원하지 않는 값 -> unresolved=true
- Kiwoom hoga 코드값은 매핑하지 않는다.

테스트:
- tests/test_order_hoga_mapper.py

### 2. OrderType Mapper

파일: order_type_mapper.py

함수: map_order_type_preview(order)

역할:
- order_intent의 매수/매도 의도를 내부 표준 order_type 값으로 preview 변환한다.
- BUY/매수 -> BUY
- SELL/매도 -> SELL
- 빈값/미확정/알 수 없는 값 -> unresolved=true
- Kiwoom 주문유형 코드값은 매핑하지 않는다.

테스트:
- tests/test_order_type_mapper.py

### 3. ExecutionController Preview

파일: execution_controller.py

함수: build_execution_preview(order, guard=None)

역할:
- REAL_READY 상태 여부를 확인한다.
- Hoga Mapper와 OrderType Mapper를 호출한다.
- adapter request preview builder 사용 가능 여부와 메모리 request preview 생성 가능 여부만 확인한다.
- 최종 실제 실행 가능 여부는 판단하지 않는다.

테스트:
- tests/test_execution_controller.py

### 4. Final Execution Guard

파일: final_execution_guard.py

함수: evaluate_final_execution_guard(order, guard, execution_preview)

역할:
- 최종 실행 직전 조건을 순수 판정한다.
- 검사 항목:
  - order.status == REAL_READY
  - order.execution_enabled == true
  - guard.operator_confirmed == true
  - guard.real_trade_enabled == true
  - hoga_preview.unresolved == false
  - order_type_preview.unresolved == false
  - execution_preview.unresolved == false

테스트:
- tests/test_final_execution_guard.py

### 5. Order Lock Preview

파일: order_lock_manager.py

함수: build_order_lock_preview(order, execution_preview)

역할:
- lock 저장 없이 lock_key/lock_id 후보만 생성한다.
- order_id, source_signal_id, code, side/order_type 필수값을 확인한다.
- side가 없으면 execution_preview.order_type_preview.order_type을 후보로 사용한다.

테스트:
- tests/test_order_lock_manager.py

### 6. Request Hash Preview

파일: order_request_hash.py

함수: build_order_request_hash_preview(order, execution_preview, lock_preview)

역할:
- 실행 요청 식별용 stable hash 후보를 생성한다.
- 필수 입력 후보:
  - order_id
  - source_signal_id
  - code
  - side/order_type
  - quantity
  - price
  - hoga
  - lock_id
- 모든 필드가 있으면 canonical JSON 기반 SHA-256 hash를 생성한다.
- 필수 필드 누락 시 unresolved=true로 반환한다.

테스트:
- tests/test_order_request_hash.py

### 7. Execution Request Preview

파일: order_execution_request.py

함수:
- build_execution_request_preview(order, guard, execution_preview, final_guard_result, lock_preview, request_hash_preview)

역할:
- execution request 후보 dict만 생성한다.
- 필수 조건:
  - execution_preview.unresolved == false
  - final_guard_result.ok == true
  - lock_preview.unresolved == false
  - request_hash_preview.unresolved == false
  - request_hash 존재
- 정상 시 execution_id, order_id, source_signal_id, lock_id, request_hash, guard_snapshot, request_preview를 포함한다.

테스트:
- tests/test_order_execution_request.py

### 8. ExecutionPipelineController

파일: execution_pipeline_controller.py

함수: run_execution_preview_pipeline(order, guard)

역할:
- 지금까지 만든 preview 컴포넌트를 하나의 진입점으로 묶는다.
- 호출 순서:
  1. build_execution_preview(order, guard)
  2. evaluate_final_execution_guard(order, guard, execution_preview)
  3. build_order_lock_preview(order, execution_preview)
  4. build_order_request_hash_preview(order, execution_preview, lock_preview)
  5. build_execution_request_preview(...)
- 첫 차단 지점을 blocked_stage로 반환한다.

테스트:
- tests/test_execution_pipeline_controller.py

### 9. ExecutionPipelineSummary

파일: execution_pipeline_summary.py

함수: summarize_execution_preview_pipeline(pipeline_result)

역할:
- pipeline result를 GUI/로그/검증 화면용 summary dict로 변환한다.
- 추출 항목:
  - ok
  - blocked_stage
  - ready_for_execution_request
  - order_id
  - execution_id
  - request_hash
  - warnings
  - blocked_reasons

테스트:
- tests/test_execution_pipeline_summary.py

### 10. ExecutionPreviewService

파일: execution_preview_service.py

함수: preview_execution_for_order(order, guard)

역할:
- GUI 버튼/CLI/테스트에서 공통으로 쓸 수 있는 수동 검증 helper다.
- 내부 동작:
  1. run_execution_preview_pipeline(order, guard)
  2. summarize_execution_preview_pipeline(pipeline_result)
  3. pipeline_result와 summary를 묶어서 반환

테스트:
- tests/test_execution_preview_service.py

## 통합 테스트

파일: tests/test_execution_preview_chain.py

검증 내용:
- REAL_READY order 1건이 전체 preview chain을 정상 통과한다.
- execution_enabled=false이면 final guard에서 차단된다.
- hoga unresolved이면 chain이 차단된다.
- request_hash 누락이면 execution request preview에서 차단된다.
- 전체 입력 dict 불변을 검증한다.

## 회귀 검증 결과

마지막 회귀 고정 결과:

- Execution Preview 계층 10개 파일 py_compile 통과
- 관련 테스트 모듈 11개 실행 통과: 72 tests
- 전체 unittest discover 통과: 117 tests

실행 명령:

```powershell
python -m py_compile order_hoga_mapper.py order_type_mapper.py execution_controller.py final_execution_guard.py order_lock_manager.py order_request_hash.py order_execution_request.py execution_pipeline_controller.py execution_pipeline_summary.py execution_preview_service.py
python -m unittest tests.test_order_hoga_mapper tests.test_order_type_mapper tests.test_execution_controller tests.test_final_execution_guard tests.test_order_lock_manager tests.test_order_request_hash tests.test_order_execution_request tests.test_execution_pipeline_controller tests.test_execution_pipeline_summary tests.test_execution_preview_service tests.test_execution_preview_chain
python -m unittest discover -s tests
```

## 반드시 유지할 금지선

다음 작업에서도 아래 금지선은 유지한다.

- SendOrder 호출 금지
- Kiwoom OpenAPI 실주문 연결 금지
- ORDER_QUEUED 생성 금지
- order_queue.json 수정 금지
- runtime/order_executions.json 생성/수정 금지
- runtime/order_locks.json 생성/수정 금지
- execution_enabled 변경 금지
- rules.json 수정 금지
- Kiwoom 코드값 직접 매핑 금지

## 다음 작업 후보

1. GUI/CLI 수동 검증 버튼 연결
2. order_queue REAL_READY 단건 조회 helper
3. Execution Preview 결과 화면 표시
4. 이후에도 SendOrder는 별도 단계까지 금지

## 다음 채팅 시작 문구 후보

Execution Preview Pipeline 1차 완료 상태에서 이어서 진행한다.

우선 금지선은 유지한다.
- SendOrder 금지
- ORDER_QUEUED 생성 금지
- runtime 파일 생성/수정 금지
- order_queue.json 수정 금지
- rules.json 수정 금지

다음 작업 후보 중 하나만 선택해서 진행한다.
1. GUI/CLI 수동 검증 버튼 연결
2. order_queue REAL_READY 단건 조회 helper
3. Execution Preview 결과 화면 표시
4. SendOrder는 아직 금지
