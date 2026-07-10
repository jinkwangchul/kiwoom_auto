# MASTER_SPEC 갱신자료 - Runtime Commit Boundary

작성일: 2026-07-09

이 문서는 MASTER_SPEC 원본을 직접 수정하지 않고, 다음 MASTER_SPEC 갱신 시 반영할 후보 내용을 정리한 자료다.

반영 기준 커밋: 6886a01
Milestone: M5 Runtime Commit Boundary Complete
검증 결과: Runtime Commit Boundary 단일 테스트 14 tests OK / 전체 unittest 2858 tests OK / 보호 파일 변경 없음

## 반영 대상 섹션 후보

### Runtime Commit Boundary

Preview-only Execution Pipeline의 최종 단계로, 실제 Runtime Commit을 수행하기 직전에
commit 후보, 적용 순서, 적용 대상, 검증 계획을 preview-only로 고정하는 경계 계층이다.

이 계층은 REAL_READY order의 preview chain 결과를 받아, real runtime 반영 전 상태를
메모리 dict로만 확정한다.

이 계층은 실제 commit 실행 계층이 아니다.

### 핵심 원칙

- Runtime Commit Boundary는 파일을 저장하지 않는다.
- Runtime Commit Boundary는 runtime/*.json을 수정하지 않는다.
- Runtime Commit Boundary는 routines/*/rules.json을 수정하지 않는다.
- Runtime Commit Boundary는 SQLite write를 하지 않는다.
- Runtime Commit Boundary는 position/balance/audit write를 하지 않는다.
- Runtime Commit Boundary는 실제 Runtime Commit을 수행하지 않는다.
- Runtime Commit Boundary는 Broker/SendOrder/Chejan/GUI에 연결하지 않는다.
- Runtime Commit Boundary는 preview_only=True만 보장한다.

### Runtime Commit Eligibility

Runtime Commit Boundary로 진입할 수 있는 최소 자격이다.

필수 자격:
- 상위 preview chain이 EXECUTION_COMMIT_PREVIEW_READY 상태여야 한다.
- execution_commit_preview.final_commit_decision.committed == true 여야 한다.
- 모든 safety flag가 False여야 한다.
- preview_only가 True여야 한다.

### Runtime Commit Contract

Runtime Commit Boundary가 생성하는 계약 dict다.

구성 컴포넌트:
- Atomic Apply Plan
- Verification Plan
- Rollback Plan

#### Atomic Apply Plan

Contract 내부에 구성되는 원자 적용 계획이다.

필수 특성:
- 적용 대상(runtime/order_queue.json, runtime/order_executions.json, runtime/order_locks.json)을 순서대로 나열한다.
- 각 step는 step_executed=False로만 표시한다.
- 실제 apply는 수행하지 않는다.
- runtime_write=False로 고정한다.

#### Verification Plan

Contract 내부에 구성되는 사후 검증 계획이다.

필수 특성:
- commit_preview_ready, runtime_apply_safety_validation_ready, apply_targets_ready 항목을 나열한다.
- 각 항목은 verification_completed=False로만 표시한다.
- 실제 검증 실행은 수행하지 않는다.

#### Rollback Plan

Contract 내부에 구성되는 롤백 계획이다.

필수 특성:
- rollback_executed=False로 고정한다.
- preview-only 상태에서는 rollback을 수행하지 않는다.
- rollback 계획은 메모리 dict 후보로만 유지한다.

### Runtime Commit Safety Gate

Runtime Commit Boundary 진입 전/후 안전 게이트다.

필수 검증:
- 상위 execution_commit_preview의 모든 safety flag가 False인지 확인한다.
- preview_only가 True인지 확인한다.
- final_commit_decision.committed가 True인지 확인한다.
- Dry-run은 Safety Gate / Review 내부 검증 성격으로만 수행한다.

### Runtime Commit Review

실제 Runtime Commit 이전 운영자 검토 단계다.

필수 특성:
- Result Review는 Runtime Commit Review로 단순화한다.
- review_completed=False로 고정한다.
- review 항목은 preview-only로만 표시한다.

## 구성 컴포넌트 (Preview Layer)

### Execution Commit Preview

파일: lifecycle_execution_commit_preview.py

함수: build_execution_commit_preview(dispatcher_preview, commit_context=None)

역할:
- EXECUTION_DISPATCHER_PREVIEW_READY 결과만 EXECUTION_COMMIT_PREVIEW_READY 생성한다.
- BLOCKED dispatcher preview는 BLOCKED commit preview 생성한다.
- INVALID/malformed dispatcher preview는 INVALID commit preview 생성한다.
- execution_commit_candidate_preview, execution_commit_route_preview, execution_commit_queue_preview, post_commit_verification_preview, commit_safety_validation, final_commit_decision을 생성한다.

테스트: tests/test_lifecycle_execution_commit_preview.py

### Execution Runtime Apply Preview

파일: lifecycle_execution_runtime_apply_preview.py

함수: build_execution_runtime_apply_preview(execution_commit_preview, apply_context=None)

역할:
- EXECUTION_COMMIT_PREVIEW_READY 결과만 EXECUTION_RUNTIME_APPLY_PREVIEW_READY 생성한다.
- BLOCKED execution commit preview는 BLOCKED runtime apply preview 생성한다.
- INVALID/malformed execution commit preview는 INVALID runtime apply preview 생성한다.
- runtime_apply_candidate_preview, runtime_apply_target_preview, runtime_apply_sequence_preview, runtime_apply_verification_preview, runtime_apply_safety_validation, final_runtime_apply_decision을 생성한다.

테스트: tests/test_lifecycle_execution_runtime_apply_preview.py

## Architecture Decision

- Runtime Commit Boundary는 Execution Preview Orchestrator 이후, Real Runtime Commit 이전에 위치한다.
- Execution Commit Preview와 Execution Runtime Apply Preview 사이에 삽입을 금지한다.
- Runtime Apply Preview 중복 생성을 금지한다.
- Atomic Apply Plan은 Contract 내부 구성 요소로 둔다.
- Dry-run은 Safety Gate / Review 내부 검증 성격으로만 둔다.
- Result Review는 Runtime Commit Review로 단순화한다.
- Preview Layer 추가 확장을 억제한다.

## 테스트 체계

### 단위 테스트

- tests/test_lifecycle_execution_dispatcher_preview.py
- tests/test_lifecycle_execution_commit_preview.py
- tests/test_lifecycle_execution_runtime_apply_preview.py

### 통합 테스트

- tests/test_lifecycle_execution_runtime_apply_preview.py (chain end-to-end)

검증:
- EXECUTION_COMMIT_PREVIEW_READY가 EXECUTION_RUNTIME_APPLY_PREVIEW_READY로 정상 변환된다.
- BLOCKED/INVALID/malformed 입력은 각각 BLOCKED/INVALID로 전파된다.
- 모든 safety flag가 False이고 preview_only=True이다.
- 보호 파일(runtime/*.json, routines/*/rules.json) hash가 변경되지 않는다.
- 입력 dict 불변이 보장된다.

## 회귀 검증 기준

Runtime Commit Boundary 변경 후 최소 검증:

```powershell
python -m py_compile lifecycle_execution_dispatcher_preview.py lifecycle_execution_commit_preview.py lifecycle_execution_runtime_apply_preview.py
python -m unittest tests.test_lifecycle_execution_dispatcher_preview tests.test_lifecycle_execution_commit_preview tests.test_lifecycle_execution_runtime_apply_preview
python -m unittest discover -s tests
```

현재 확인된 결과:

- Runtime Commit Boundary 단일 테스트: 14 tests 통과
- 전체 unittest: 2858 tests 통과
- 보호 파일 변경: 없음

## 금지선

다음 금지선은 Runtime Commit Boundary 작업에서 유지한다.

- runtime/*.json write 없음
- routines/*/rules.json 수정 없음
- SQLite write 없음
- Broker/SendOrder/Chejan/GUI 연결 없음
- 실제 Runtime Commit 수행 없음

## MASTER_SPEC 반영 시 주의

Runtime Commit Boundary의 committed=true는 실제 Runtime Commit 수행 허가를 의미하지 않는다.
실제 Runtime Commit은 별도 단계, 별도 승인, 별도 테스트, 별도 운영 안전장치가 준비될 때까지 금지한다.

Preview Layer 추가 확장도 억제한다.
