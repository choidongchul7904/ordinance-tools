# CONTRACT — signature-sheet

> 이 스킬은 **말단 빌더**입니다. ordinance-suite가 4종 일괄 생성 때 호출합니다.
> 인터페이스 변경은 아래 소비 스킬에 파급됩니다. 변경 전 이 계약과 회귀 목록을 갱신하세요.

- **정본 호출명**: `signature-sheet`   ← 반드시 이 표기로만 위임 (I2)
- **역할(1줄)**: 지방의회 안건 서명부(발의의원 서명부) HWPX를 내장 공식 양식(쪽당 15명)으로 만드는 스킬
- **버전**: v1  (소비 스킬은 이 메이저에 핀)
- **안정성**: STABLE — 하위호환 깨는 변경은 v2로만

## 소비 스킬 (consumers, 1)
- ordinance-suite (`scripts/run_suite.py` `job_sign` → `scripts/build_sheet.py`)

## 인터페이스 (변경 시 반드시 갱신)
- **입력(inputs)**: config.json
  - `안건명`(str), `대표발의`(str, 성명만), `제목`(선택), `pages`(선택 int)
  - `members`: `{"소속","성명"}` 객체 또는 `"소속|성명"` 문자열. **`|` 없는 문자열은 성명**(소속 공란, 2026-10-12)
  - `lead_in_list`(선택 bool, 기본 false): true면 대표발의를 1번 자리에 한 번만, false면 명단에 있으면 경고 후 제외
- **출력(outputs)**: 스테이징 HWPX(반드시 `com_save.py`로 한/글 정품 재저장). 표는 쪽마다 16행 고정,
  쪽수 = ceil(인원/15)(0명이면 2쪽). 연번은 전 행에 연속(빈 행 포함 — 원본 양식 관행). **서명 열은 항상 공란.**
- **부작용(side-effects)**: .hwpx 저장. 중복 성명·대표발의 중복은 stdout `!!` 경고로 보고.

## 회귀
- `python tests/test_sheet.py` (COM 없음) — 이름만 준 항목이 성명 열로, 대표발의 중복 제거, 연번 1~30 연속, 서명 열 공란.
- 쪽수 자동 증감 0·15·16·30·31·45·46명 정상(2026-10-12 감사 확인).

## 변경 정책
1. 입출력 스키마를 바꾸면 → 버전을 올리고 ordinance-suite의 `job_sign`을 함께 갱신.
2. 이름을 바꾸면 → ordinance-suite 참조를 동시 수정 후 `skill-lint`로 I1/I2=0 확인.
3. 삭제하려면 → ordinance-suite에서 서명부 작업을 먼저 대체/제거(고아 참조 금지).
