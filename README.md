# ordinance-tools

지방의회 **조례안 발의 서류를 한글(HWPX)로 자동 생성**하는 Claude Code 플러그인 마켓플레이스입니다.

수록 플러그인: **`ordinance-kit`** — 조례안 하나를 넣으면 문서 4종을 한 번에 만듭니다.

| # | 산출물 | 규격 |
|---|---|---|
| 1 | 조례안 **완성본(의안 서식)** | 의안 표지 + 제안설명서 + 조례안 본문 2구역 |
| 2 | 조례안 **표준 입안 서식** | 「조례만들기」 규격 (조 14 / 호 24.4 / 목 45.4pt 내어쓰기) |
| 3 | **입법예고 의뢰서** | 제정·일부개정·전부개정 3종 분기 |
| 4 | **발의의원 서명부** | 쪽당 15명, 인원수에 따라 쪽 자동 증감 |

## 설치

Claude Code 대화창에서:

```
/plugin marketplace add choidongchul7904/ordinance-tools
/plugin install ordinance-kit@ordinance-tools
```

설치 요약에 `Run /reload-plugins to activate.` 가 표시되면 `/reload-plugins` 를 실행합니다.

로컬에서 먼저 시험해 보려면 저장소를 내려받아:

```bash
claude --plugin-dir ./ordinance-kit
```

## 요구 환경

| 항목 | 필요 이유 |
|---|---|
| **Windows + 한/글(HWP) 설치** | COM으로 '정품' HWPX 저장. 이 단계를 건너뛴 파일은 한/글 보안수준 '높음'에서 열리지 않습니다 |
| Python 3.9+ | 빌더 스크립트 |
| `lxml` | HWPX(XML) 편집 |
| `pywin32` | 한/글 COM 제어 |
| `pypdfium2` | 검증용 PDF 렌더 |

```bash
python -m pip install lxml pywin32 pypdfium2
```

**글꼴** — 없어도 파일은 만들어지지만 서식이 달라 보입니다. 경기천년제목 Medium·경기천년바탕 Regular(입법예고), HY견고딕·휴먼명조(완성본), 신명조(표준 입안 서식).

## 사용법

대화창에 조례안 파일을 주며 "이 조례안으로 발의 서류 4종 만들어줘" 라고 하면 `ordinance-suite` 스킬이 잡힙니다. 스크립트를 직접 쓰려면:

```bash
python parse_ordinance.py "조례안.hwpx" suite.json
```

```bash
python run_suite.py suite.json --outdir "출력폴더" --pdf
```

`--only 완성본,입법예고` 로 일부만 만들 수 있습니다. 출력 폴더를 지정하지 않으면 환경변수 `ORDINANCE_OUT_DIR`, 없으면 현재 폴더에 저장합니다.

빠진 항목(소관위원회·공고번호·공동발의 명단 등)은 `suite.json` 의 `_미확정` 에 모여 있으니 채운 뒤 실행하세요. 스키마는 `skills/ordinance-suite/references/spec-schema.md`, 예시는 `skills/ordinance-suite/examples/example.suite.json` 입니다.

## 서식의 출처와 적용 범위 — 반드시 읽어 주세요

- 내장 서식은 **경기도의회 규격**을 기준으로 만들어졌습니다. 문단 인덱스·글꼴·내어쓰기 수치가 그 규격에 맞춰져 있습니다.
- 내장 서식 파일은 **실물 문서의 내용을 모두 지우고 `○○` 자리표시자만 남긴 빈 골격**입니다. 실제 조례안 내용·의원 성명·문서번호는 들어 있지 않습니다.
- 다만 입법예고 의뢰서의 **의견제출 안내 박스(보내는곳·팩스·전자우편)는 서식 고정값이라 경기도의회 법제과 연락처가 그대로 들어 있습니다.** 다른 의회에서 쓰려면 `skills/ipbeop-yego/assets/` 의 서식 파일을 해당 의회 서식으로 교체하세요.
- 예고문·공고문의 의회명은 `suite.json` 의 `의회명` 으로 바꿀 수 있습니다(미지정 시 `경기도의회`). 문의처는 `문의처` 에 부서·전화만 넣으면 됩니다.
- **이 도구는 서식 조판만 담당합니다.** 조문의 적법성·상위법 저촉·중복 조례·비용추계는 검토하지 않습니다. 제출 전 소관 부서·법제 담당의 검토를 반드시 거치세요.
- 산출물은 PDF로 렌더해 **육안으로** 확인하세요. 잘림·겹침은 텍스트 검사로 잡히지 않습니다.

## 공개 전에 채울 자리

이 저장소를 그대로 포크해 배포한다면 아래를 먼저 바꾸세요.

| 파일 | 자리표시자 |
|---|---|
| `.claude-plugin/marketplace.json` | `최동철`, `choidongchul7904` |
| `ordinance-kit/.claude-plugin/plugin.json` | `최동철`, `choidongchul7904` |
| `LICENSE`, `ordinance-kit/LICENSE` | `최동철` |
| `README.md` | 설치 명령의 `choidongchul7904` |

## 저장소 구성

```
ordinance-tools/
├── .claude-plugin/marketplace.json     마켓플레이스 카탈로그
├── ordinance-kit/                      플러그인
│   ├── .claude-plugin/plugin.json
│   └── skills/
│       ├── ordinance-suite/            4종 일괄 생성 오케스트레이터
│       ├── ordinance-uian/             완성본(의안 서식)
│       ├── ordinance-format/           표준 입안 서식
│       ├── ipbeop-yego/                입법예고 의뢰서
│       └── signature-sheet/            발의의원 서명부
├── LICENSE
└── README.md
```

## 라이선스

코드·문서는 MIT. 내장 서식(HWPX)은 각 의회가 배포한 공문서 서식을 자리표시자로 비운 것으로, 서식 자체의 권리는 해당 기관에 있습니다.
