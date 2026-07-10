# M6 Real Runtime Commit Architecture 설계 보고

작성일: 2026-07-09
반영 기준 커밋: 6886a01
이전 Milestone: M5 Runtime Commit Boundary Complete
전체 unittest: 2858 tests OK
작업 성격: 설계 보고만 수행 (구현 금지)

---

## 0. 분석 대상 요약

| 대상 | 경로 | 역할 |
|------|------|------|
| Runtime Commit Boundary | [`runtime_commit_boundary.py`](runtime_commit_boundary.py:1) | M5 완료. READY 이후 실제 commit 진입 전 경계 확정 (preview-only) |
| Runtime Commit Contract | [`runtime_commit_boundary.py:142`](runtime_commit_boundary.py:142) | atomic_apply_plan / verification_plan / rollback_plan / protected_targets 생성 |
| Runtime Write Preview | [`execution_runtime_write_preview.py:1`](execution_runtime_write_preview.py:1) | order_executions/order_locks 기록 후보 preview (write 미수행) |
| Runtime Commit Service (기존 real) | [`execution_runtime_commit_service.py:1`](execution_runtime_commit_service.py:1) | order_executions/order_locks에 execution+lock 기록 append (실제 write 존재) |
| Real Commit Readiness Policy | [`execution_runtime_real_commit_readiness_policy.py:1`](execution_runtime_real_commit_readiness_policy.py:1) | project-runtime commit 개방 여부 결정 (preview-only) |
| Runtime File Init Commit Service | [`execution_runtime_file_init_commit_service.py:1`](execution_runtime_file_init_commit_service.py:1) | order_executions/order_locks 파일 최초 생성 |
| Runtime File Schema | [`execution_runtime_file_schema.py:1`](execution_runtime_file_schema.py:1) | ORDER_EXECUTIONS_SCHEMA / ORDER_LOCKS_SCHEMA 정의 |
| Runtime Reader | [`execution_runtime_reader.py:1`](execution_runtime_reader.py:1) | read_order_executions / read_order_locks (read-only) |
| Queue Commit Executor (참조 패턴) | [`execution_queue_commit_executor.py:1`](execution_queue_commit_executor.py:1) | order_queue.json atomic write + backup + rollback + verify 참조 구현 |
| Rule Commit Report Service (audit 참조) | [`rule_commit_report_service.py:1`](rule_commit_report_service.py:1) | commit 결과 audit report 기록 패턴 |

### runtime/*.json 현재 존재 현황 (중요)

`list_files` 및 직접 확인 결과:

- 존재: `runtime/order_queue.json`, `runtime/operation_state.json`, `runtime/routine_signals.json`, `runtime/routine_signal_probe.log`
- **존재하지 않음**: `runtime/order_executions.json`, `runtime/order_locks.json`

즉, M6 Real Runtime Commit이 `order_executions.json` / `order_locks.json`에 실제 write를 수행하려면
**파일 부재 상태를 먼저 해결**해야 한다. 이는 `execution_runtime_file_init_commit_service.py` 영역이지만,
현재 이 서비스도 project-runtime 경로에서는 `file_init_open_policy` + 수동 확인이 없으면 BLOCKED다.
따라서 M6 설계에서 "파일 초기화 선행 조건"을 명시적으로 다뤄야 한다.

---

## 1. 현재 구조 분석

### 1.1 Runtime Commit Boundary (M5) 출력 계약

[`runtime_commit_boundary.py:419`](runtime_commit_boundary.py:419) 의 `_result` 가 반환하는 dict 구조:

```
preview_type: "RUNTIME_COMMIT_BOUNDARY"
status: RUNTIME_COMMIT_BOUNDARY_READY | BLOCKED | INVALID
runtime_commit_eligibility
runtime_commit_contract
  - contract_status
  - commit_candidate
  - atomic_apply_plan (5 steps: lock_runtime → apply_order_queue → apply_order_executions → apply_order_locks → unlock_runtime)
  - verification_plan (4 items)
  - rollback_plan (3 steps: restore_order_queue / restore_order_executions / restore_order_locks)
  - protected_targets: [order_queue.json, order_executions.json, order_locks.json, routines/*/rules.json]
runtime_commit_safety_gate
runtime_commit_review
runtime_commit_boundary_summary
final_runtime_commit_boundary_decision (ready/blocked/invalid + 모든 safety flag = False)
```

핵심: Boundary는 **모든 safety flag를 False로 고정**하며 `runtime_write=False` 다.
즉 Boundary는 "commit 진입 자격 + 계획"만 확정하고, 실제 write는 수행하지 않는다.

### 1.2 기존 실제 write 계층 (이미 존재)

`execution_runtime_commit_service.py` 는 이미 실제 write를 수행하는 서비스다:

- [`commit_execution_runtime_plan`](execution_runtime_commit_service.py:270) : orchestrator 결과 검증 → 수동 확인 → 대상 경로 확인 → **backup → atomic write → read-back verify → rollback on failure**
- atomic write: [`_write_json_atomic`](execution_runtime_commit_service.py:213) (temp + fsync + os.replace)
- backup: [`_make_backup`](execution_runtime_commit_service.py:230) (`{path}.bak`)
- rollback: [`_restore_backups`](execution_runtime_commit_service.py:236)
- verify: [`_read_back_contains`](execution_runtime_commit_service.py:255)

그러나 이 서비스는 **project-runtime 경로에서는 `real_commit_readiness_policy_result` + `manual_project_runtime_commit_confirmed` 가 없으면 BLOCKED** 다 ([`execution_runtime_commit_service.py:129`](execution_runtime_commit_service.py:129)).
또한 대상 파일이 없으면 `MISSING_ORDER_EXECUTIONS_FILE` / `MISSING_ORDER_LOCKS_FILE` 로 BLOCKED 다 ([`execution_runtime_commit_service.py:183`](execution_runtime_commit_service.py:183)).

### 1.3 Queue Commit Executor (참조 패턴)

[`execution_queue_commit_executor.py:221`](execution_queue_commit_executor.py:221) 는 더 성숙한 패턴을 보여준다:

- commit_id 기반 backup 명명: `{name}.{commit_id}.bak` (충돌/누적 방지)
- before/after sha256 hash 기록
- `_verify_queue_item` 사후 검증
- 실패 시 `_restore_backup` + `manual_restore_required` 플래그
- `next_stage` 전이 (REVIEW_REQUIRED)

이 패턴을 M6 Real Runtime Commit의 표준으로 삼는다.

### 1.4 분석 결론

- Boundary(M5)는 "계획/자격" 계층, 기존 commit service는 "실행" 계층으로 이미 분리되어 있다.
- **문제점**: 기존 `execution_runtime_commit_service.py` 가 Executor + Writer + Backup + Rollback + Verifier 역할을 **한 파일에 모두** 섞고 있다. M6는 이를 관심사별로 분리된 6개 후보 계층으로 재구성하는 것이 목적이다.
- **누락**: Audit Record 계층이 별도로 없다 (rule_commit_report_service는 rules 전용). Runtime commit 전용 audit 기록 계층이 필요하다.
- **누락**: `order_executions.json` / `order_locks.json` 파일 부재 처리 계획이 M6 진입 전에 명확하지 않다.

---

## 2. Real Runtime Commit 권장 위치

```
Execution Preview Orchestrator
        ↓ (ORCHESTRATOR_READY)
Runtime Commit Boundary  (M5, preview-only)
        ↓ (RUNTIME_COMMIT_BOUNDARY_READY + contract)
Real Commit Readiness Policy (이미 존재, preview-only, project-runtime 개방 결정)
        ↓ (READY_TO_OPEN_RUNTIME_COMMIT + manual confirmations)
[M6] Runtime Commit Executor  ← 진입점
        ├─ Runtime Backup Manager
        ├─ Atomic Runtime Writer
        ├─ Runtime Commit Verifier
        ├─ Runtime Rollback Manager
        └─ Runtime Commit Audit Record
```

권장 위치 원칙:

1. **Runtime Commit Executor** 가 M5 Boundary 결과(`runtime_commit_contract`)를 입력으로 받아,
   Real Commit Readiness Policy 통과 + 수동 확인 이후 **최초로 실제 write를 시작하는 단일 진입점**이 된다.
2. Executor는 직접 파일을 쓰지 않고, 하위 5개 계층(Writer/Backup/Rollback/Verifier/Audit)을 조율(orchestrate)만 한다.
3. 기존 `execution_runtime_commit_service.py` 는 점진적으로 Executor + 하위 계층으로 분해하되,
   M6 설계 단계에서는 **신규 모듈로 추가**하고 기존 서비스는 호환 유지(금지 규정상 수정하지 않음).

---

## 3. 계층 구성안 (6개 후보 계층)

| # | 계층 | 신규 모듈 후보명 | 책임 범위 |
|---|------|------------------|-----------|
| 1 | Runtime Commit Executor | `runtime_commit_executor.py` | 진입점. 계약 수락, 순서 조율, 하위 계층 호출, 최종 결과 취합 |
| 2 | Atomic Runtime Writer | `runtime_atomic_writer.py` | temp+fsync+replace 원자 쓰기 유틸 (순수 함수) |
| 3 | Runtime Backup Manager | `runtime_backup_manager.py` | backup 생성/조회/만료, commit_id 기반 명명 |
| 4 | Runtime Rollback Manager | `runtime_rollback_manager.py` | backup으로부터 복원, 복원 실패 시 manual_restore_required |
| 5 | Runtime Commit Verifier | `runtime_commit_verifier.py` | write 전 스키마 검증 + write 후 read-back 검증 |
| 6 | Runtime Commit Audit Record | `runtime_commit_audit_record.py` | commit 결과 audit dict/report 기록 (read-only 저장은 금지 → dict만 생성) |

---

## 4. 각 계층 책임

### 4.1 Runtime Commit Executor
- 입력: `runtime_commit_boundary` 결과(dict), `real_commit_readiness_policy_result`(dict), 대상 경로, 수동 확인 컨텍스트
- 책임:
  - Boundary `status == RUNTIME_COMMIT_BOUNDARY_READY` 확인
  - contract 내 `atomic_apply_plan` step 순서 준수 보장
  - Backup Manager → Writer → Verifier 호출, 실패 시 Rollback Manager 호출
  - 최종 Audit Record 생성 및 결과 dict 반환
- **금지**: 직접 파일 IO 수행 금지 (Writer에게 위임)

### 4.2 Atomic Runtime Writer
- 책임: [`execution_runtime_commit_service.py:213`](execution_runtime_commit_service.py:213) 의 `_write_json_atomic` 패턴을 순수 유틸로 분리
  - `write_json_atomic(path, data)` : temp 파일 생성 → json dump → fsync → os.replace → temp 정리
- **금지**: backup 생성, rollback, 검증 로직 포함 금지

### 4.3 Runtime Backup Manager
- 책임:
  - `create_backup(path, commit_id)` → `{path}.{commit_id}.bak` 생성 (queue executor 패턴)
  - `list_backups(target_key)` / `get_backup_path(target_key, commit_id)`
  - backup 생성 실패 시 호출자에게 BLOCKED 전파
- **금지**: 복원 수행 금지 (Rollback Manager 전용)

### 4.4 Runtime Rollback Manager
- 책임:
  - `restore_from_backup(backup_paths)` → 대상 파일로 복원
  - 복원 성공/실패 반환, 실패 시 `manual_restore_required=True`
  - 복원 대상: order_executions.json, order_locks.json (contract rollback_plan 기준)
- **금지**: 새 backup 생성 금지

### 4.5 Runtime Commit Verifier
- 책임:
  - **사전 검증(pre-write)**: [`execution_runtime_reader.py`](execution_runtime_reader.py:1) 로 스키마 유효성 (`read_order_executions` / `read_order_locks` 의 `ok`)
  - **사후 검증(post-write)**: write 직후 read-back 하여 기록 포함 여부 확인 ([`execution_runtime_commit_service.py:255`](execution_runtime_commit_service.py:255) `_read_back_contains` 패턴)
  - before/after sha256 hash 비교 (queue executor 패턴)
- **금지**: write/rollback 수행 금지

### 4.6 Runtime Commit Audit Record
- 책임:
  - commit 결과를 audit dict로 구성 (rule_commit_report_service [`_REPORT_FIELDS`](rule_commit_report_service.py:23) 패턴 차용)
  - 필드: commit_id, stage, committed, target_paths, backup_paths, before/after hash, rollback_attempted, rollback_succeeded, restored_from_backup, manual_restore_required, issues, warnings
  - **금지**: 파일/SQLite 저장 금지 (dict 생성만). 실제 저장은 별도 단계에서 (금지 규정 준수)

---

## 5. 입력/출력 계약

### 5.1 Executor 입력 계약
```python
{
  "boundary_result": dict,            # evaluate_runtime_commit_boundary() 결과
  "real_commit_readiness_policy_result": dict,  # READY_TO_OPEN_RUNTIME_COMMIT
  "order_executions_path": str,
  "order_locks_path": str,
  "context": dict,                    # manual confirmations
  "commit_id": str,                   # 신규 생성 또는 호출자 제공
  "backup": bool = True,
}
```

### 5.2 Executor 출력 계약 (기존 service 결과와 호환)
```python
{
  "service_type": "RUNTIME_COMMIT_EXECUTOR",
  "status": "COMMITTED" | "BLOCKED" | "INVALID" | "ERROR",
  "runtime_write": bool,
  "committed": bool,
  "commit_id": str,
  "order_executions_path": str,
  "order_locks_path": str,
  "backup_paths": dict,
  "before_hashes": dict,
  "after_hashes": dict,
  "read_back_verified": bool,
  "rollback_attempted": bool,
  "rollback_succeeded": bool,
  "restored_from_backup": list,
  "manual_restore_required": bool,
  "audit_record": dict,               # Runtime Commit Audit Record 출력
  "issues": list,
  "warnings": list,
}
```

---

## 6. write / backup / rollback / verification / audit 정책

### 6.1 write 정책
- 대상: `runtime/order_executions.json` (executions append), `runtime/order_locks.json` (locks append)
- `order_queue.json` 은 Boundary contract의 atomic_apply_plan step 2(apply_order_queue)에 명시되어 있으나,
  **실제 queue write는 `execution_queue_commit_executor.py` 전용** 이므로 M6 Real Runtime Commit은
  order_queue.json을 직접 쓰지 않는다. (충돌 방지 — 7절 참조)
- 원자성: temp + fsync + os.replace (process crash 안전)
- append-only: 기존 리스트에 record 추가, 전체 덮어쓰기 아님

### 6.2 backup 정책
- 시점: write 직전 1회 (all-or-nothing)
- 명명: `{target}.{commit_id}.bak` (queue executor 표준, 누적/충돌 방지)
- 범위: order_executions.json, order_locks.json (protected_targets 기준)
- 실패: backup 생성 실패 시 즉시 BLOCKED, write 미수행

### 6.3 rollback 정책
- 트리거: write 예외 OR 사후 검증 실패
- 범위: backup 생성된 파일만 (all-or-nothing)
- 결과: `rollback_succeeded` / `manual_restore_required` 명시
- `routines/*/rules.json` 은 M6 대상 아님 (protected, 절대 touch 금지)

### 6.4 verification 정책
- 사전: reader로 스키마 `ok` 확인, duplicate 검증 (execution_id/request_hash/order_id, lock_id/request_hash/order_id)
- 사후: read-back contains 확인 + before/after sha256 비교
- 실패 시 rollback 자동 호출

### 6.5 audit 정책
- 기록 위치: **dict 생성만** (파일/SQLite 저장 금지 — 금지 규정 준수)
- 권장 향후 저장 위치 후보: `runtime/runtime_commit_audit.json` (별도 구현 단계에서, 금지 위반 아님이나 본 설계는 저장 안 함)
- 내용: commit_id, 대상, backup 경로, hash, rollback 상태, issues

---

## 7. 기존 구조와 충돌 여부

| 항목 | 충돌 여부 | 설명 |
|------|-----------|------|
| `execution_runtime_commit_service.py` | **부분 중복** | Executor가 동일 역할을 수행. M6는 신규 모듈로 추가하고 기존은 유지(수정 금지). 향후 리팩터 후보 |
| `execution_queue_commit_executor.py` | **충돌 없음** | order_queue.json 전용. M6는 executions/locks 전용. backup/hash 패턴만 차용 |
| `execution_runtime_file_init_commit_service.py` | **선행 의존** | executions/locks 파일 부재 시 M6 진입 불가. File Init 선행 필요 |
| `execution_runtime_real_commit_readiness_policy.py` | **의존** | M6 진입 전 통과 필수. 재사용 |
| `runtime_commit_boundary.py` (M5) | **충돌 없음** | M6의 직접 상위 입력. contract 구조 그대로 소비 |
| `routines/*/rules.json` | **보호 대상** | M6는 절대 수정하지 않음 (protected_targets) |
| Preview 계층 일괄 | **충돌 없음** | 모든 preview는 preview_only=True 유지, M6는 그 경계 이후 실행 |

**핵심 충돌 회피 원칙**: M6 Real Runtime Commit은 `order_queue.json` 을 쓰지 않는다.
Boundary contract의 `apply_order_queue` step은 preview 계획상 존재하나 실제 write 주체는 Queue Commit Executor다.
따라서 M6는 executions/locks 에만 책임을 한정하여 이중 write 충돌을 방지한다.

---

## 8. 다음 구현 최소 단위 제안

구현 금지나, 다음 단계를 위한 **최소 단위 제안** 만 기술한다.

### 단계 A (기반 유틸 분리)
1. `runtime_atomic_writer.py` — `write_json_atomic(path, data)` 순수 유틸
   (기존 [`execution_runtime_commit_service.py:213`](execution_runtime_commit_service.py:213) 이식)
2. `runtime_backup_manager.py` — `create_backup(path, commit_id)` (queue executor 명명 규칙)

### 단계 B (검증/롤백)
3. `runtime_commit_verifier.py` — pre/post 검증 (reader + sha256 + read-back)
4. `runtime_rollback_manager.py` — `restore_from_backup(backup_paths)`

### 단계 C (감사/실행)
5. `runtime_commit_audit_record.py` — audit dict 생성 (저장 제외)
6. `runtime_commit_executor.py` — 위 5개 조율 진입점, Boundary 결과 소비

### 단계 D (연결/검증)
7. Executor가 `real_commit_readiness_policy_result` + 수동 확인을 요구하도록 wiring
8. File Init 선행 보장 (executions/locks 파일 존재 확인 또는 File Init 호출)
9. 단위 테스트: 각 계층 isolated test + Executor 통합 test (temp 경로 사용)

### 구현 우선순위
- **최소 가치 단위**: 단계 A-1 (`runtime_atomic_writer.py`) — 가장 순수하고 재사용성 높음, 기존 6개 모듈에 중복된 로직 통합 효과
- **리스크 최소**: temp 경로 전용으로 시작, project-runtime 경로는 기존 policy/수동 확인 게이트 유지

---

## 9. MASTER_SPEC 반영 후보

본 설계는 추후 다음 MASTER_SPEC 갱신자료 후보로 정리 가능:

- 신규 섹션: "Real Runtime Commit Architecture (M6)"
- 핵심 원칙 후보:
  - Real Runtime Commit은 Boundary(READY) 이후, Real Commit Readiness Policy 통과 + 수동 확인 후에만 진입한다.
  - Real Runtime Commit은 order_executions.json / order_locks.json 에만 write한다 (order_queue.json 아님).
  - 모든 write는 temp+fsync+replace 원자 쓰기다.
  - 모든 write는 write 직전 commit_id 기반 backup을 생성한다.
  - write 실패 또는 사후 검증 실패 시 backup으로부터 rollback한다.
  - routines/*/rules.json은 protected 대상으로 절대 수정하지 않는다.
  - Runtime Commit Audit Record는 commit 결과를 dict로 생성한다 (저장은 별도 단계).

> 본 문서는 MASTER_SPEC을 직접 수정하지 않는다 (금지 규정 준수). 갱신자료화는 별도 단계에서 수행.

---

## 10. 분석 항목 1~11 직접 응답

1. **Boundary READY 이후 실제 commit 진입 조건**: `status==RUNTIME_COMMIT_BOUNDARY_READY` + `real_commit_readiness_policy_result.status==READY_TO_OPEN_RUNTIME_COMMIT` + 수동 확인 2종(`manual_execution_runtime_commit_confirmed`, `manual_runtime_file_write_confirmed`) + 대상 파일 존재(또는 File Init 선행).
2. **실제 write 담당 최소 계층**: `Atomic Runtime Writer` (순수 유틸) + 이를 호출하는 `Runtime Commit Executor`.
3. **atomic write 방식**: temp 파일(`{name}.{uuid}.tmp`) → json dump → `os.fsync` → `os.replace` → temp 정리.
4. **backup 생성 위치/조건**: write 직전, `{target}.{commit_id}.bak` 형태, executions/locks 대상, backup 실패 시 BLOCKED.
5. **rollback 조건/범위**: write 예외 또는 사후 검증 실패 시, backup된 files만 복원, 실패 시 `manual_restore_required`.
6. **verification 기준**: 사전 스키마 `ok` + duplicate 검증, 사후 read-back contains + before/after sha256 일치.
7. **audit 기록 여부/위치**: dict 생성만 (저장 금지). 향후 `runtime/runtime_commit_audit.json` 후보.
8. **protected runtime 파일별 write 정책**: executions/locks = append write 허용; order_queue = 미작성(Queue Executor 전용); operation_state/routine_signals = 미작성; rules.json = 절대 미작성.
9. **기존 Preview/Boundary 충돌 여부**: 충돌 없음. M6는 Boundary 이후 실행, 모든 preview는 preview_only 유지.
10. **MASTER_SPEC 반영 후보**: 9절 후보 원칙 참조.
11. **구현 최소 단위**: 8절 단계 A-1 (`runtime_atomic_writer.py`) 부터 시작.
