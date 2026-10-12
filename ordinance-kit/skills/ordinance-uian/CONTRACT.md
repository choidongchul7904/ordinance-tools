# CONTRACT — ordinance-uian

> 이 스킬은 의안 서식 빌더이자 **신ㆍ구조문대비표 모듈(compare_table.py)의 공급원**이다.
> 인터페이스 변경은 아래 소비 스킬에 파급된다. 변경 전 이 계약과 회귀 테스트
> (`tests/test_uian.py`)를 갱신하라.

- **정본 호출명**: `ordinance-uian`   ← 반드시 이 표기로만 위임 (I2)
- **역할(1줄)**: 지방의회 의원 발의 조례안 완성본(의안 서식 — 표지·제안설명서·조문·부칙, 개정안은 대비표) HWPX를 만드는 스킬
- **버전**: v1  (소비 스킬은 이 메이저에 핀)
- **안정성**: STABLE — 하위호환 깨는 변경은 v2로만

## 소비 스킬 (consumers) — 2026-10-12 grep 실측

| 소비 스킬 | 쓰는 것 | 방식 |
|---|---|---|
| `ordinance-suite` | `scripts/build_uian.py spec.json stage.hwpx` → `com_save.py` | 서브프로세스(`run_suite.py` `job_uian`). spec = suite.json 그대로 |
| `ordinance-format` | `scripts/compare_table.py` — `append_to_file`·`verify_block`·`clean_pairs`·`UNVERIFIED` | import(`ORDINANCE_SKILLS_DIR` 또는 형제 폴더의 `ordinance-uian/scripts`) |
| `ordinance-lint` | 문서 안내만 — 수정 결과를 의안 문서로 만들 때 이 스킬로 넘긴다(ordinance-lint/CONTRACT.md 연계 표) | 위임 안내(코드 호출 없음) |

`com_save.py`는 ordinance-suite·ipbeop-yego·signature-sheet에 **바이트 동일 사본**이 있다
(공용 모듈이 아니라 사본 — 고치면 4개를 함께 고치고 md5를 맞출 것; `tests/test_uian.py`가 검사).

## 인터페이스 (변경 시 반드시 갱신)

### build_uian.py
- **CLI**: `python build_uian.py spec.json stage.hwpx [--template PATH] [--buchik-break off|on] [--compare auto|off] [--keep-newnum]`
- **입력(spec)**: `조례명`(필수), `대표발의`(필수), `발의연월일`, `발의자`(str|list), `공동발의`,
  `공포기관`, `제안이유`[], `주요내용`[], `참고사항`[], `본칙`[{level,text}], `부칙`[{level,text}],
  `대비표`[[현행, 개정안]], `대비표제목`.
  개정(2026-10-12, 하위호환 추가 — ordinance-suite와 합의한 공유 규약):
  `유형` ∈ {제정, 일부개정, 전부개정}(옛 값 `개정`도 수용). 제명 판별 규칙 = 공백 제거 후 `전부개정` →
  전부개정, `일부개정` 또는 `…개정조례안/개정규칙안` 끝 → 일부개정, 그 밖 → 제정. 명시값 우선, 단 제명이
  개정인데 다르면 중단. `현행조례명`(「」 없이), `개정문`[{level,text}](두문 제외 — 빌더가 렌더, 첫 줄이 이미
  두문이면 덧붙이지 않음; 없으면 `본칙`).
  level ∈ {장, 절, 조, 지시, 항, 호, 목, 1), 가), (1), (가)} — 그 밖·`text` 누락은 오류(exit≠0).
- **출력**: 스테이징 HWPX(반드시 `com_save.py`로 정품 저장). stdout 첫 줄 `stage: … (… bytes, 유형, 조문 N개[, 신ㆍ구조문대비표 N행][, [새 번호 지정] N개 제거])`,
  이어서 경고·`[미검증 …]` 목록(대화창 보고용). 실패 시 SystemExit 메시지·exit≠0.

### compare_table.py (모듈 API — ordinance-format이 의존)
- `insert_into(dst_hdr, sec, pairs, anchor=None, font_override=None, title=TITLE)`
- `append_block(items, section_name, pairs, before=None, font_override=None, title=TITLE)`
- `append_to_file(src_hwpx, dst_hwpx, pairs, section=0, font_override=None, title=TITLE)`
- `verify_block(section_xml_bytes, pairs) -> 행 수` — 표식·마커 잔존, 행 수 검사(셀 가드 반영)
- `clean_pairs(pairs, report=True) -> pairs` — 미검증 마커 셀을 공란으로, `UNVERIFIED`에 보고 적재
- `UNVERIFIED`(list), `TITLE`(기본 `신ㆍ구조문대비표`)
- CLI: `python compare_table.py in.hwpx out.hwpx pairs.json [--section N] [--font 글꼴] [--title 제목]`

### com_save.py
- **CLI**: `python com_save.py stage.hwpx out.hwpx [out.pdf]` — 성공 시 stdout `saved:`·(`pdf:`)·`PageCount:`.
  실패 시 exit≠0, 이전 out은 보존(임시 파일 저장 후 `os.replace`). PDF 실패는 stderr 경고(HWPX는 저장).

- **부작용(side-effects)**: 스테이징/산출 .hwpx(.pdf) 파일 쓰기. 한/글 COM은 com_save에서만.

## 변경 정책
1. spec·CLI·compare_table API를 바꾸면 → 버전을 올리고 ordinance-suite·ordinance-format을 함께 갱신,
   `tests/test_uian.py`와 `ordinance-format/tests/test_format.py`를 모두 통과시킨다.
2. 유형 판별 규칙은 ordinance-suite·ordinance-format과 **같은 문장**을 유지한다(각 빌더에 인라인 사본).
3. 이름을 바꾸면 → 소비 스킬 참조를 동시 수정 후 `skill-lint`로 I1/I2=0 확인.
4. 삭제하려면 → 소비 스킬을 먼저 대체/제거(고아 참조 금지). 특히 compare_table.py는 format이 import한다.
