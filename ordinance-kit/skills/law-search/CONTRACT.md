# CONTRACT — law-search

> 이 스킬은 **허브**입니다(의존 6개). 인터페이스 변경은
> 아래 소비 스킬 전부에 파급됩니다. 변경 전 이 계약과 회귀 목록을 갱신하세요.

- **정본 호출명**: `law-search`   ← 반드시 이 표기로만 위임 (I2)
- **역할(1줄)**: 국가법령정보센터 Open API(law.go.kr DRF)로 현행 법령·판례를 실시간 검색·본문조회하는 스킬
- **버전**: v1  (소비 스킬은 이 메이저에 핀)
- **안정성**: STABLE — 하위호환 깨는 변경은 v2로만

## 소비 스킬 (consumers, 6)
- ipbeop-yego

- law-local

- law-thesis-completion

- law-thesis-structurer

- ordinance-suite

- signature-sheet

## 인터페이스 (변경 시 반드시 갱신)
- **입력(inputs)**: 법령명·키워드·조문 식별자(OC는 env LAW_OC)
- **출력(outputs)**: 현행법령·시행일법령·판례·행정규칙 JSON(공포일·시행일·소관부처·조문 원문)
- **부작용(side-effects)**: 없음(law.go.kr 읽기 전용 API)

## 변경 정책
1. 입출력 스키마를 바꾸면 → 버전을 올리고 소비 스킬의 핀을 갱신.
2. 이름을 바꾸면 → 위 소비 스킬 6곳의 참조를 동시 수정 후 `skill-lint`로 I1/I2=0 확인.
3. 삭제하려면 → 소비 스킬을 먼저 대체/제거(고아 참조 금지).
