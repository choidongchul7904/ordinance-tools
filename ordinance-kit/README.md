# ordinance-kit

조례안 하나로 발의 서류 4종(완성본·표준 입안 서식·입법예고 의뢰서·발의의원 서명부)을 한글(HWPX)로 만드는 Claude Code 플러그인.

설치·요구 환경·사용법·적용 범위는 저장소 최상위 [README](../README.md)를 보세요.

## 수록 스킬

| 스킬 | 역할 |
|---|---|
| `ordinance-suite` | 조례안 → 통합 스펙 추출 → 4종 일괄 생성 (진입점) |
| `ordinance-uian` | 조례안 완성본(의안 서식) |
| `ordinance-format` | 조례안 표준 입안 서식 |
| `ipbeop-yego` | 입법예고 의뢰서(제정·개정) |
| `signature-sheet` | 발의의원 서명부 |

플러그인 스킬은 이름 앞에 플러그인 이름이 붙습니다 — 예: `/ordinance-kit:ordinance-suite`.
설명 기반 자동 호출은 그대로 동작하므로, 조례안을 첨부하고 "발의 서류 만들어줘" 라고 해도 됩니다.

## 개발

```bash
claude --plugin-dir ./ordinance-kit
```

```bash
claude plugin validate ./ordinance-kit
```
