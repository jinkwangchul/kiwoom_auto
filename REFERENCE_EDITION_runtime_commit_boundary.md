# Reference Edition - Execution Preview Pipeline & Runtime Commit Boundary

생성일: 2026-07-09
최종 갱신 기준 커밋: 6886a01
Milestone: M5 Runtime Commit Boundary Complete

이 문서는 프로젝트의 Execution Preview Pipeline 및 Runtime Commit Boundary 관련
MASTER_SPEC 갱신자료와 작업재개요약을 하나로 모은 Reference Edition이다.

원본이 되는 갱신자료/요약 문서는 다음과 같다.
- MASTER_SPEC_갱신자료_execution_preview_pipeline_1차.md
- 작업재개요약_execution_preview_pipeline_1차.md
- MASTER_SPEC_갱신자료_runtime_commit_boundary.md
- 작업재개요약_runtime_commit_boundary.md

상세 원문은 위 파일을 우선한다. 이 문서는 검색/참조용 요약본이다.

## 0. 개요

실주문 연결 전 단계에 Preview Layer를 둔다. 이 계층은 실제 주문/commit/runtime 반영을
수행하지 않고, 변환/검증/후보 생성을 메모리 dict로만 한다.

핵심 금지선:
- SendOrder 호출 금지
- Kiwoom OpenAPI 실주문 연결 금지
- ORDER_QUEUED 생성 금지
- order_queue.json 수정 금지
- runtime/order_executions.json 생성/수정 금지
- runtime/order_locks.json 생성/수정 금지
- routines/*/rules.json 수정 금지
- SQLite write 금지
- Broker/SendOrder/Chejan/GUI 연결 금지
- 실제 Runtime Commit 수행 금지

## 1. Execution Preview Pipeline 1차 (갱신자료 1차)

### 구성 컴포넌트

1. Hoga Mapper (order_hoga_mapper.py) - map_order_hoga_preview
2. OrderType Mapper (order_type_mapper.py) - map_order_type_preview
3. ExecutionController Preview (execution_controller.py) - build_execution_preview
4. Final Execution Guard (final_execution_guard.py) - evaluate_final_execution_guard
5. Order Lock Preview (order_lock_manager.py) - build_order_lock_preview
6. Request Hash Preview (order_request_hash.py) - build_order_request_hash_preview
7. Execution Request Preview (order_execution_request.py) - build_execution_request_preview
8. ExecutionPipelineController (execution_pipeline_controller.py) - run_execution_preview_pipeline
9. ExecutionPipelineSummary (execution_pipeline_summary.py) - summarize_execution_preview_pipeline
10. ExecutionPreviewService (execution_preview_service.py) - preview_execution_for_order

### 검증 (1차)

- related: 72 tests OK
- 전체: 117 tests OK

## 2. Runtime Commit Boundary (갱신자료 2차)

### 위치 결정 (Architecture Decision)

- Execution Preview Orchestrator 이후, Real Runtime Commit 이전.
- Execution Commit Preview와 Execution Runtime Apply Preview 사이 삽입 금지.
- Runtime Apply Preview 중복 생성 금지.
- Atomic Apply Plan은 Contract 내부 구성.
- Dry-run은 Safety Gate / Review 내부 검증 성격.
- Result Review는 Runtime Commit Review로 단순화.
- Preview Layer 추가 확장 억제.

### Runtime Commit Eligibility

- 상위 chain이 EXECUTION_COMMIT_PREVIEW_READY.
- final_commit_decision.committed == true.
- 모든 safety flag가 False.
- preview_only == True.

### Runtime Commit Contract

- Atomic Apply Plan: 적용 대상/순서, step_executed=False, runtime_write=False.
- Verification Plan: 사후 검증 항목, verification_completed=False.
- Rollback Plan: rollback_executed=False, 메모리 후보만 유지.

### Runtime Commit Safety Gate

- 상위 preview safety flag 전부 False 확인.
- preview_only True 확인.
- final_commit_decision.committed True 확인.
- Dry-run은 게이트/리뷰 내부 검증.

### Runtime Commit Review

- Result Review -> Runtime Commit Review 단순화.
- review_completed=False 고정.

### Preview Layer 컴포넌트

1. Execution Dispatcher Preview (lifecycle_execution_dispatcher_preview.py)
   - build_execution_dispatcher_preview(final_approval_preview, dispatcher_context=None)
   - READY -> EXECUTION_DISPATCHER_PREVIEW_READY
2. Execution Commit Preview (lifecycle_execution_commit_preview.py)
   - build_execution_commit_preview(dispatcher_preview, commit_context=None)
   - READY -> EXECUTION_COMMIT_PREVIEW_READY
3. Execution Runtime Apply Preview (lifecycle_execution_runtime_apply_preview.py)
   - build_execution_runtime_apply_preview(execution_commit_preview, apply_context=None)
   - READY -> EXECUTION_RUNTIME_APPLY_PREVIEW_READY

각 preview는 READY/BLOCKED/INVALID/malformed 입력을 각각 READY/BLOCKED/INVALID로 전파한다.

### 테스트 (2차)

- 단위: tests/test_lifecycle_execution_dispatcher_preview.py
- 단위: tests/test_lifecycle_execution_commit_preview.py
- 단위/통합: tests/test_lifecycle_execution_runtime_apply_preview.py

### 검증 (2차)

- Runtime Commit Boundary 단일 테스트: 14 tests OK
- 전체 unittest: 2858 tests OK
- 보호 파일 변경: 없음

## 3. 공통 Safety 고정값

모든 preview layer 결과는 다음 safety flag를 False로 고정하고 preview_only=True를 보장한다.

- preview_only=True
- dispatch_allowed / dispatch_started / dispatch_completed = False
- execution_commit_allowed / execution_commit_started / execution_commit_completed = False
- runtime_apply_allowed / runtime_apply_started / runtime_apply_completed = False
- execution_allowed / execution_started / execution_completed = False
- send_order_called / send_order_result_recorded = False
- recorder_called / chejan_called = False
- runtime_write / position_write / balance_write / audit_write = False
- file_write_called / gui_update_called = False
- backup_created / rollback_executed = False

## 4. 회귀 검증 명령

```powershell
python -m py_compile lifecycle_execution_dispatcher_preview.py lifecycle_execution_commit_preview.py lifecycle_execution_runtime_apply_preview.py
python -m unittest tests.test_lifecycle_execution_dispatcher_preview tests.test_lifecycle_execution_commit_preview tests.test_lifecycle_execution_runtime_apply_preview
python -m unittest discover -s tests
```
