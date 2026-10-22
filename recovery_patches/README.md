# SOR 실매매 캐시 300봉 수정 — 독립 복원 패치

기준 Git HEAD: `75296031aec709ef032e329db5c5af27a5d928e8`

이 브랜치는 **원본 작업 트리의 다른 731개 미커밋 항목을 포함하지 않는 복원 전용 브랜치**입니다. 실제 `gui_market_data_host.py` 전체를 커밋하면 기존 미커밋 구현까지 포함되므로, 이번 변경의 정확한 패치만 보관합니다. 실행 가능한 전체 소스 브랜치라고 간주하면 안 됩니다.

## 보관 내용
- `sor_live_cache_300_host.patch`: 실매매 M1 고정 600/700봉 하한 제거, M5 기준 6분 M1 유지, 등록되지 않은 종목 기본 600 제거. V5 관찰자의 별도 전체 세션 요청은 유지합니다.
- `test_live_cache_scope_300.py`: 실매매 기준봉 300개/M1 6개, 보관량, V5 관찰자 병행 회귀 테스트 6개.

## 복원·적용 조건
1. 원래 미커밋 작업 내용이 보존된 `gui_market_data_host.py`가 있어야 합니다. `7529603`의 깨끗한 파일에 바로 적용할 수 있는 패치가 아닙니다.
2. 이번 수정 **직전**의 해당 파일 SHA256:
   `B18ED0A03579A77478F8F4C9CA331390B542C88C0FBDDD590F0338B648D7EB0F`
3. 원본을 별도 백업한 뒤, 그 위치에서:
   `git apply --check <patch path>`
   `git apply <patch path>`
4. 회귀 테스트 파일을 기존 `tests/` 아래에 복사하고 `py -3.11-32 -m pytest -q tests/test_live_cache_scope_300.py`로 확인합니다.
5. 수정 전 원본 백업: `C:\Users\JIN KWANG CHUL\AppData\Local\Temp\kiwoom-gui_market_data_host-before-m1-split-20261023.py`
6. 수정 후 호스트 및 관련 테스트 사본: `C:\Users\JIN KWANG CHUL\AppData\Local\Temp\kiwoom-live-cache-300-20261023`

검증: 패치를 수정 전 백업 복사본에 적용한 뒤 현재 호스트 파일과 `git diff --no-index`로 비교하여 차이 없음. 실매매/캐시 관련 선택 테스트 97개 및 하위 11개 통과(모의검증 별도 실패 1건 제외). V5 관련 테스트 272개 통과, 별도 원천 선택 2건 실패. 실패 원인은 이 패치와의 인과관계가 확정되지 않았습니다.

## 제한
기존 테스트의 기대값 수정은 원본 작업 트리에 남아 있으며 복원용 로컬 사본에도 있습니다. 이 복원 브랜치는 그 테스트 파일들의 전체 미커밋 내용을 전송하지 않습니다. 직접 매매 주문은 실행하지 않았습니다.
