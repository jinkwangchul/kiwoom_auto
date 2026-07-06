# MASTER_SPEC 갱신자료 - Execution Preview Pipeline 1차

작성일: 2026-07-03

이 문서는 MASTER_SPEC 원본을 직접 수정하지 않고, 다음 MASTER_SPEC 갱신 시 반영할 후보 내용을 정리한 자료다.

## 반영 대상 섹션 후보

### Execution Preview Layer

실주문 연결 전 단계에 Execution Preview Layer를 둔다.

이 계층은 REAL_READY order 1건을 대상으로 SendOrder 직전까지 필요한 변환, 검증, 잠금 후보, 요청 hash 후보, execution request 후보를 모두 메모리 dict로만 만든다.

이 계층은 실제 주문 실행 계층이 아니다.

### 핵심 원칙

- Preview Layer는 파일을 저장하지 않는다.
- Preview Layer는 order_queue.json을 수정하지 않는다.
- Preview Layer는 runtime/order_locks.json을 생성하거나 수정하지 않는다.
- Preview Layer는 runtime/order_executions.json을 생성하거나 수정하지 않는다.
- Preview Layer는 execution_enabled 값을 변경하지 않는다.
- Preview Layer는 rules.json을 수정하지 않는다.
- Preview Layer는 ORDER_QUEUED를 생성하지 않는다.
- Preview Layer는 SendOrder를 호출하지 않는다.
- Preview Layer는 Kiwoom OpenAPI 코드값을 직접 매핑하지 않는다.

### 구성 컴포넌트

#### Hoga Mapper

파일: order_hoga_mapper.py

함수: map_order_hoga_preview(order)

역할:
- order_intent의 호가 의도를 내부 표준 hoga preview 값으로 변환한다.
- 시장가 -> MARKET
- 현재가/지정가 -> LIMIT
- 미확정/빈값/알 수 없는 값은 unresolved=true로 유지한다.

금지:
- Kiwoom hoga 코드값 직접 매핑 금지

#### OrderType Mapper

파일: order_type_mapper.py

함수: map_order_type_preview(order)

역할:
- order_intent의 매수/매도 의도를 내부 표준 order_type preview 값으로 변환한다.
- BUY/매수 -> BUY
- SELL/매도 -> SELL
- 미확정/빈값/알 수 없는 값은 unresolved=true로 유지한다.

금지:
- Kiwoom 주문유형 코드값 직접 매핑 금지

#### ExecutionController Preview

파일: execution_controller.py

함수: build_execution_preview(order, guard=None)

역할:
- order.status가 REAL_READY인지 확인한다.
- Hoga Mapper와 OrderType Mapper를 호출한다.
- Adapter request preview builder 사용 가능 여부를 확인한다.
- guard가 dict로 주어지면 adapter request preview를 메모리에서만 생성할 수 있다.
- 최종 실행 가능 여부는 판단하지 않는다.

#### Final Execution Guard

파일: final_execution_guard.py

함수: evaluate_final_execution_guard(order, guard, execution_preview)

역할:
- 최종 실행 직전 조건을 순수 판정한다.
- 차단 사유는 blocked_reasons에 모은다.

필수 조건:
- order.status == REAL_READY
- order.execution_enabled == true
- guard.operator_confirmed == true
- guard.real_trade_enabled == true
- hoga_preview.unresolved == false
- order_type_preview.unresolved == false
- execution_preview.unresolved == false

#### Order Lock Preview

파일: order_lock_manager.py

함수: build_order_lock_preview(order, execution_preview)

역할:
- 실제 lock 저장 없이 lock_key/lock_id 후보만 생성한다.
- 필수값:
  - order_id
  - source_signal_id
  - code
  - side/order_type

금지:
- runtime/order_locks.json 생성/수정 금지

#### Request Hash Preview

파일: order_request_hash.py

함수: build_order_request_hash_preview(order, execution_preview, lock_preview)

역할:
- execution request 식별용 stable hash 후보를 생성한다.
- 모든 필수 필드가 있으면 canonical JSON 기반 SHA-256 hash를 생성한다.

필수값:
- order_id
- source_signal_id
- code
- side/order_type
- quantity
- price
- hoga
- lock_id

금지:
- runtime/order_executions.json 생성/수정 금지

#### Execution Request Preview

파일: order_execution_request.py

함수:
- build_execution_request_preview(order, guard, execution_preview, final_guard_result, lock_preview, request_hash_preview)

역할:
- execution request 후보 dict만 생성한다.
- 실제 execution 기록 저장은 하지 않는다.

필수 조건:
- execution_preview.unresolved == false
- final_guard_result.ok == true
- lock_preview.unresolved == false
- request_hash_preview.unresolved == false
- request_hash 존재

반환 후보:
- execution_id
- order_id
- source_signal_id
- lock_id
- request_hash
- guard_snapshot
- request_preview

#### ExecutionPipelineController

파일: execution_pipeline_controller.py

함수: run_execution_preview_pipeline(order, guard)

역할:
- Preview Layer의 단일 진입점이다.
- REAL_READY order 1건을 SendOrder 직전 preview까지 순서대로 평가한다.
- 첫 차단 지점을 blocked_stage로 반환한다.

호출 순서:
1. build_execution_preview(order, guard)
2. evaluate_final_execution_guard(order, guard, execution_preview)
3. build_order_lock_preview(order, execution_preview)
4. build_order_request_hash_preview(order, execution_preview, lock_preview)
5. build_execution_request_preview(...)

차단 기준:
- execution_preview.unresolved == true -> blocked_stage="execution_preview"
- final_guard.ok != true -> blocked_stage="final_guard"
- lock_preview.unresolved == true -> blocked_stage="lock_preview"
- request_hash_preview.unresolved == true -> blocked_stage="request_hash_preview"
- execution_request_preview.unresolved == true -> blocked_stage="execution_request_preview"

#### ExecutionPipelineSummary

파일: execution_pipeline_summary.py

함수: summarize_execution_preview_pipeline(pipeline_result)

역할:
- pipeline 결과를 GUI/로그/검증 화면용 summary dict로 변환한다.

요약 필드:
- ok
- blocked_stage
- ready_for_execution_request
- order_id
- execution_id
- request_hash
- warnings
- blocked_reasons

#### ExecutionPreviewService

파일: execution_preview_service.py

함수: preview_execution_for_order(order, guard)

역할:
- GUI 버튼, CLI, 테스트에서 공통으로 사용할 수 있는 수동 검증 helper다.
- pipeline_result와 summary를 함께 반환한다.

동작:
1. run_execution_preview_pipeline(order, guard)
2. summarize_execution_preview_pipeline(pipeline_result)
3. 두 결과를 묶어서 반환

## 테스트 체계

### 단위 테스트

- tests/test_order_hoga_mapper.py
- tests/test_order_type_mapper.py
- tests/test_execution_controller.py
- tests/test_final_execution_guard.py
- tests/test_order_lock_manager.py
- tests/test_order_request_hash.py
- tests/test_order_execution_request.py
- tests/test_execution_pipeline_controller.py
- tests/test_execution_pipeline_summary.py
- tests/test_execution_preview_service.py

### 통합 테스트

- tests/test_execution_preview_chain.py

검증:
- REAL_READY order 1건이 전체 preview chain을 정상 통과한다.
- execution_enabled=false이면 final guard에서 차단된다.
- hoga unresolved이면 chain이 차단된다.
- request_hash 누락이면 execution request preview에서 차단된다.
- 입력 dict 불변을 검증한다.

## 회귀 검증 기준

Execution Preview Layer 변경 후 최소 검증:

```powershell
python -m py_compile order_hoga_mapper.py order_type_mapper.py execution_controller.py final_execution_guard.py order_lock_manager.py order_request_hash.py order_execution_request.py execution_pipeline_controller.py execution_pipeline_summary.py execution_preview_service.py
python -m unittest tests.test_order_hoga_mapper tests.test_order_type_mapper tests.test_execution_controller tests.test_final_execution_guard tests.test_order_lock_manager tests.test_order_request_hash tests.test_order_execution_request tests.test_execution_pipeline_controller tests.test_execution_pipeline_summary tests.test_execution_preview_service tests.test_execution_preview_chain
python -m unittest discover -s tests
```

현재 확인된 결과:

- 관련 테스트: 72 tests 통과
- 전체 테스트: 117 tests 통과

## 다음 작업 후보

1. GUI/CLI 수동 검증 버튼 연결
2. order_queue REAL_READY 단건 조회 helper
3. Execution Preview 결과 화면 표시
4. SendOrder는 별도 승인 단계까지 계속 금지

## MASTER_SPEC 반영 시 주의

Execution Preview Layer는 실주문 실행 설계가 아니라, 실주문 연결 전 검증/요약/후보 생성 계층이다.

따라서 MASTER_SPEC에는 다음 문장을 명시하는 것이 안전하다.

"Execution Preview Layer의 ok=true는 SendOrder 호출 허가를 의미하지 않는다. 실제 SendOrder 연결은 별도 단계, 별도 승인, 별도 테스트, 별도 운영 안전장치가 준비될 때까지 금지한다."
