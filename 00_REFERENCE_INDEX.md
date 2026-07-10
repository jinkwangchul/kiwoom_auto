# 00 Reference Index

최종 갱신: 2026-07-09
갱신 기준 커밋: 6886a01
Milestone: M5 Runtime Commit Boundary Complete

이 문서는 프로젝트 문서 참조용 인덱스다. MASTER_SPEC 원본은 직접 수정하지 않으며,
갱신은 `MASTER_SPEC_갱신자료_*.md` 후보 문서로 관리한다.

## 문서 목록

### 갱신자료 (MASTER_SPEC Update Candidate)

| 파일 | 주제 | 작성일 |
|------|------|--------|
| MASTER_SPEC_갱신자료_execution_preview_pipeline_1차.md | Execution Preview Pipeline 1차 | 2026-07-03 |
| MASTER_SPEC_갱신자료_runtime_commit_boundary.md | Runtime Commit Boundary | 2026-07-09 |

### 작업재개요약 (Continuation Summary)

| 파일 | 주제 | 작성일 |
|------|------|--------|
| 작업재개요약_execution_preview_pipeline_1차.md | Execution Preview Pipeline 1차 | 2026-07-03 |
| 작업재개요약_runtime_commit_boundary.md | Runtime Commit Boundary | 2026-07-09 |

### Reference Edition

| 파일 | 주제 | 작성일 |
|------|------|--------|
| REFERENCE_EDITION_runtime_commit_boundary.md | Execution Preview Pipeline + Runtime Commit Boundary 통합본 | 2026-07-09 |

## 변경 이력 (Changelog)

상세 변경 이력은 PROJECT_CHANGELOG.txt를 따른다.

## 현재 Milestone

- M5 Runtime Commit Boundary Complete
- 검증: Runtime Commit Boundary 단일 테스트 14 tests OK / 전체 unittest 2858 tests OK / 보호 파일 변경 없음

## 금지선 요약

- runtime/*.json write 없음
- routines/*/rules.json 수정 없음
- SQLite write 없음
- Broker/SendOrder/Chejan/GUI 연결 없음
- 실제 Runtime Commit 수행 없음
- Preview Layer 추가 확장 억제
