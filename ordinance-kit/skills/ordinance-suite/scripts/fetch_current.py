# -*- coding: utf-8 -*-
"""개정조례 입법예고에 필요한 **현행 조례 전문**과 **신·구조문 대비표 초안**을
국가법령정보센터 API에서 받아 suite.json 에 채운다.

개정조례 입법예고 의뢰서는 「참고 1 현행 조례」와 「신·구조문 대비표」가 있어야
만들어진다. 그동안 이 두 블록은 사람이 직접 채워야 해서 `run_suite.py` 가
"현행 조례 전문과 신·구조문 대비표가 필요합니다" 로 실패했다.

  python fetch_current.py suite.json --org 경기도 --inplace
  python fetch_current.py suite.json --조례명 "경기도 ○○ 조례" --out suite_filled.json

채우는 것
  현행조례   API 조문 원문 전체 (장·절 제목 포함 — 참고 1은 '전문'이다)
  대비표     개정문에서 대상 조를 읽어 **현행 열만** 자동으로 채운다.
             자구 수정(`"A"를 "B"로 한다`)은 치환을 적용해 개정안 열까지 만든다.
             신설·삭제·이동은 **사람이 쓰도록 자리표시자를 남긴다** — 지어내지 않는다.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ordlaw  # noqa: E402

LAWSEARCH = Path.home() / ".claude" / "skills" / "law-search" / "scripts" / "lawsearch.py"

RE_JO_HEAD = re.compile(r"^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
RE_HEADING = re.compile(r"^\s*제\s*\d+\s*[장절관편](?:\s|$)")
# 개정 지시문에서 대상 조 추출: "제10조제2항 중 …", "제5조를 다음과 같이 신설한다"
RE_TARGET = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
# 자구 수정: 제N조… 중 "A"를 "B"로 한다
RE_REPLACE = re.compile(r"[“\"']([^”\"']{1,120})[”\"']\s*를?\s*"
                        r"[“\"']([^”\"']{1,120})[”\"']\s*로\s*한다")
RE_NEW = re.compile(r"신설한다|신설")
RE_DEL = re.compile(r"삭제한다|삭제")

# 미완성 자리는 **검색 가능한 유일 토큰**으로 남긴다. 사람이 읽는 한글 문장으로 두면
# 본문과 섞여 검사에서 빠져나가고, 경고는 언젠가 무시된다. run_suite 가 이 토큰을
# 발견하면 입법예고 빌드를 **차단**한다.
TODO_NEW = "[[TODO:신설조문]]"
TODO_AMD = "[[TODO:개정안조문]]"


def call(args):
    r = subprocess.run([sys.executable, str(LAWSEARCH)] + args,
                       capture_output=True, timeout=90)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    try:
        data = json.loads(out) if out else None
    except Exception:
        return None, "응답 파싱 실패: %s" % out[:160]
    if r.returncode == 2:
        return None, "결과 없음"
    if r.returncode != 0:
        return None, (data or {}).get("error", "API 오류(exit %d)" % r.returncode)
    return data, None


def find_ordinance(name, org=None):
    """조례명 → (자치법규일련번호, 메타). 같은 이름이 여러 지자체에 있으므로 org로 좁힌다."""
    data, err = call(["search", "--target", "ordin", "--query", name])
    if not data:
        return None, err
    cands = [r for r in data.get("results", [])
             if (r.get("자치법규명") or "").strip() == name.strip()]
    if not cands:
        near = [r.get("자치법규명") for r in data.get("results", [])[:5]]
        return None, "정확 일치 없음. 후보: %s" % ", ".join(near)
    if org:
        cands = [r for r in cands if (r.get("지자체기관명") or "") == org] or cands
    if len(cands) > 1:
        print("!! 동명 조례 %d건 — %s 선택(--org 로 지정 가능)"
              % (len(cands), cands[0].get("지자체기관명")), file=sys.stderr)
    r = cands[0]
    return {"mst": str(r.get("자치법규일련번호")), "명": r.get("자치법규명"),
            "기관": r.get("지자체기관명"), "시행일자": r.get("시행일자"),
            "공포일자": r.get("공포일자")}, None


def fetch_body(mst):
    """자치법규 전문 → [{level,text}] (참고 1에 실을 형태)."""
    data, err = call(["law", "--target", "ordin", "--mst", mst])
    if not data:
        return None, err
    out = []
    for a in data.get("조문", []):
        t = (a.get("조문원문") or a.get("조문내용") or "").strip()
        if not t:
            continue
        # 줄바꿈은 항·호 경계다(law-search 가 넣어 준다). 뭉개면 대비표가 통짜가 된다.
        out.append({"level": "조", "text": "\n".join(
            " ".join(ln.split()) for ln in t.split("\n") if ln.strip())})
    return out, None


def jo_of(text):
    m = RE_JO_HEAD.match(text or "")
    if not m:
        return None
    return "%s의%s" % (m.group(1), m.group(2)) if m.group(2) else m.group(1)


def build_table_ast(개정문, body, style="dash"):
    """AST 엔진 — 지시문을 편집 연산으로 컴파일해 적용한 뒤, 두 트리를 대비한다.

    신설·삭제·조번호 이동이 특별 처리 없이 diff 결과로 나온다.
    실측 골든(경기도의회 21번 일부개정조례안) 대비 행수 16/16, 셀 일치 14/16.
    """
    cur = ordlaw.parse(body)
    ops, warn = ordlaw.compile_ops(개정문)
    if not ops:
        return None, ["지시문을 하나도 해석하지 못했습니다"], warn
    new_ast, errs = ordlaw.apply_ops(cur, ops, strict=False)
    rows = ordlaw.table(cur, new_ast, style=style)
    return rows, errs, warn


def build_table(개정문, body):
    """단순 엔진 (폴백) — 자구 수정만 반영하고 나머지는 [[TODO:]] 로 남긴다."""
    by_jo = {}
    for a in body:
        j = jo_of(a["text"])
        if j and j not in by_jo:
            by_jo[j] = a["text"]

    rows, todo = [], []
    for e in 개정문:
        line = (e.get("text") or "").strip()
        if not line:
            continue
        m = RE_TARGET.search(line)
        if not m:
            continue
        jo = "%s의%s" % (m.group(1), m.group(2)) if m.group(2) else m.group(1)
        cur = by_jo.get(jo)

        if cur is None:
            rows.append(["<신  설>", "%s ← %s" % (TODO_NEW, line[:60])])
            todo.append("제%s조 신설" % jo)
            continue

        rep = RE_REPLACE.search(line)
        if rep and not RE_NEW.search(line):
            a, b = rep.group(1), rep.group(2)
            if a in cur:
                rows.append([cur, cur.replace(a, b)])
                continue
            raise SystemExit("\n".join([
                "G1 정적검사 실패 — 제%s조에 “%s” 가 현행 조문에 없습니다." % (jo, a[:40]),
                "  지시문: %s" % line[:100],
                "  조번호를 잘못 썼거나 현행 조례가 이미 개정된 것입니다."
                " 확인 후 다시 실행하세요."]))

        if RE_DEL.search(line) and not RE_NEW.search(line):
            rows.append([cur, "삭  제"])
            continue

        rows.append([cur, "%s ← %s" % (TODO_AMD, line[:60])])
        todo.append("제%s조 개정안 열" % jo)
    return rows, todo


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    # 게이트 메시지는 stderr 로 나간다. cp949 로 잡히면 한글이 깨져 읽을 수 없고,
    # 읽을 수 없는 차단 메시지는 차단하지 않는 것과 같다.
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--조례명", dest="name", help="미지정 시 spec의 현행조례명 사용")
    ap.add_argument("--org", help="지자체명 (동명 조례 구분)")
    ap.add_argument("--out")
    ap.add_argument("--inplace", action="store_true")
    ap.add_argument("--engine", choices=["ast", "simple"], default="ast",
                    help="ast=지시문 컴파일(신설·삭제·이동 반영), simple=자구수정만")
    ap.add_argument("--table-style", dest="style", choices=["dash", "full"], default="dash",
                    help="dash=바뀐 부분만 쓰고 나머지는 줄표(실무 관행), full=전문")
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    name = a.name or spec.get("현행조례명") or re.sub(
        r"\s*(일부|전부)?개정조례안$", "", spec.get("조례명", "")).strip()
    if not name:
        raise SystemExit("현행 조례명을 알 수 없습니다 — --조례명 으로 지정하세요.")

    meta, err = find_ordinance(name, a.org or spec.get("공포기관"))
    if not meta:
        raise SystemExit("현행 조례 조회 실패: 「%s」 — %s" % (name, err))
    print("현행 조례: 「%s」 %s · 시행 %s · MST %s"
          % (meta["명"], meta["기관"], meta["시행일자"], meta["mst"]))

    body, err = fetch_body(meta["mst"])
    if not body:
        raise SystemExit("조문 조회 실패: %s" % err)
    print("현행 조문 %d개 확보" % len(body))

    spec["현행조례명"] = meta["명"]
    spec["현행조례"] = body

    개정문 = spec.get("개정문") or []
    if 개정문:
        rows = todo = None
        if a.engine == "ast":
            rows, errs, warn = build_table_ast(개정문, body, a.style)
            for w in warn:
                print("   ! 컴파일 경고: %s" % w, file=sys.stderr)
            for e in errs:
                print("   ! 적용 오류: %s" % e, file=sys.stderr)
            if rows is None:
                print("!! AST 엔진 실패 — simple 엔진으로 대체합니다.", file=sys.stderr)
        if rows is None:
            rows, todo = build_table(개정문, body)

        directives = [e for e in 개정문
                      if RE_TARGET.search((e.get("text") or ""))
                      and (e.get("text") or "").rstrip().endswith("한다.")]
        if rows and len(rows) < len(directives):
            print("!! G2 커버리지 경고 — 지시문 %d개 대비 대비표 %d행. 누락 가능."
                  % (len(directives), len(rows)), file=sys.stderr)
        if rows:
            spec["대비표"] = rows
            auto = sum(1 for r in rows if "[[TODO:" not in r[1])
            print("대비표 %d행 생성 [%s 엔진] (자동 %d행 · 수기 %d행)"
                  % (len(rows), a.engine, auto, len(rows) - auto))
            for t in (todo or []):
                print("   ! %s" % t)
    else:
        print("!! spec에 `개정문`이 없어 대비표는 만들지 않았습니다.")

    dst = a.spec if a.inplace else (a.out or "suite_filled.json")
    Path(dst).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장: %s" % dst)
    left = [r for r in spec.get("대비표", []) if "[[TODO:" in r[1]]
    if left:
        print("※ [[TODO:…]] %d건이 남았습니다. 채우기 전에는 입법예고 빌드가 차단됩니다"
              " (초안 회람용이면 run_suite 에 --allow-todo)." % len(left))


if __name__ == "__main__":
    main()
