# CONTRACT — ordinance-format

> 인터페이스 변경은 아래 소비 스킬에 파급됩니다. 변경 전 이 계약과 회귀 테스트
> (`tests/test_format.py`)를 갱신하세요.

- **정본 호출명**: `ordinance-format`   ← 반드시 이 표기로만 위임 (I2)
- **역할(1줄)**: 조례안 표준 입안 서식('조례만들기' 규격)에 맞는 한글(HWPX) 문서를 생성하는 스킬
- **버전**: v1  (소비 스킬은 이 메이저에 핀)
- **안정성**: STABLE — 하위호환 깨는 변경은 v2로만

## 소비 스킬 (consumers) — 2026-10-12 grep 실측
- **ordinance-suite** — `scripts/run_suite.py`의 `job_format`이 `build_ordinance.py`를 서브프로세스로
  호출한다(유일한 코드 호출자).
- 문서 안내만(코드 호출 없음): ordinance-uian·ipbeop-yego·signature-sheet·ordinance-lint·ordinance-draft의
  SKILL.md/CONTRACT.md가 "표준 입안 서식은 ordinance-format"으로 위임 안내한다. ipbeop-yego·signature-sheet는
  `build_ordinance.py`를 import/실행하지 않는다(이전 계약의 '소비 4개'는 문서 참조를 센 것이었다).

## 역의존 (이 스킬이 기대는 것)
- **ordinance-uian/scripts/compare_table.py** — spec `compare_table`(대비표) 삽입에 쓴다
  (`append_to_file`·`verify_block`·`clean_pairs`·`UNVERIFIED`). 위치는 `ORDINANCE_SKILLS_DIR`
  (없으면 형제 폴더). 이 모듈의 시그니처가 바뀌면 format이 깨진다 — ordinance-uian/CONTRACT.md 참조.

## 인터페이스 (변경 시 반드시 갱신)
- **입력(inputs)**: spec.json — `title`(필수), `body`[{level,text}], `addenda`, `promulgation_line`,
  `output`, `pdf_preview`, `compare_table`([[현행, 개정안], …]), `compare_title`. 또는 `--template`(빈 서식).
  개정(2026-10-12, 하위호환 추가): `유형`(또는 `type`) ∈ {제정, 일부개정, 전부개정} — 제명에서 자동 판별
  (공백 제거 후 `전부개정`→전부개정, `일부개정`·`…개정조례안/개정규칙안`→일부개정), 명시값 우선이되
  제명(개정)과 다르면 중단; `현행조례명`(또는 `current_title`, 「」 없이);
  `개정문`(또는 `amendment`, 없으면 `body`) = 두문을 뺀 지시 줄+삽입 조문. 두문은 빌더가 렌더하며
  첫 줄이 이미 두문이면 덧붙이지 않는다.
  level ∈ {장, 절, 조, 지시, 항, 호, 목, 1), 가), (1), (가)} — 그 밖·`text` 누락은 오류로 중단.
- **출력(outputs)**: 표준 입안 서식 HWPX(조항호목 7단계 내어쓰기; 개정조례안은 두문 자동,
  `compare_table`이 있으면 신ㆍ구조문대비표 포함). 출력 폴더 기본값 = 환경변수 `ORDINANCE_OUT_DIR`
  (없으면 `출력폴더`).
- **부작용(side-effects)**: .hwpx(+.pdf) 저장(한글 COM 정품저장). 미검증 항목은 공란 처리 후 stdout 보고만.

## 변경 정책
1. 입출력 스키마를 바꾸면 → 버전을 올리고 소비 스킬의 핀을 갱신.
2. 이름을 바꾸면 → 위 소비 스킬의 참조를 동시 수정 후 `skill-lint`로 I1/I2=0 확인.
3. 삭제하려면 → 소비 스킬을 먼저 대체/제거(고아 참조 금지).
