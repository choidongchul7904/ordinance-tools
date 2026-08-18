#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""law-search — 국가법령정보 Open API(law.go.kr DRF)로 현행 법령·판례 검색/본문 조회.

조례안 입안·검토가 상위법·현행 조례를 실제 API로 검증·인용(허위 인용 방지)하는 데 쓴다.
반환값의 시행일자/공포일자/조문원문을 그대로 인용하면 근거가 검증 가능해진다.
가이드: https://open.law.go.kr/LSO/openApi/guideList.do

사용:
  python lawsearch.py search --target law  --query "저작권법"      # 현행법령 검색
  python lawsearch.py search --target prec --query "저작권"         # 판례 검색
  python lawsearch.py law    --mst 283335 [--jo 25] [--jo 7의2]     # 법령 본문(특정 조문)
  python lawsearch.py prec   --id 618155                           # 판례 본문
  공통: --oc <기관코드>  또는 환경변수 LAW_OC  --display 20 --page 1  [--out result.json]
target: law(현행법령)·eflaw(시행일법령)·prec(판례)·detc(헌재결정례)·expc(법령해석례)·admrul(행정규칙)·ordin(자치법규)
종료코드: 0 정상 / 2 결과없음 / 3 네트워크·API 오류(우아한 강등 — 조용히 통과 금지, 사용자에게 명시)

DRF 응답의 함정(이 스크립트가 흡수한다):
  * 결과가 1건이면 배열이 아니라 객체로 온다 — 검색결과·조문단위·항·호·목 모든 층에서
    똑같이 일어난다. 반드시 _as_list()로 정규화해서 쓸 것.
  * 결과 노드 키가 target 이름과 다르다 — ordin·eflaw는 "law", detc는 "Detc"(대문자).
  * 항이 있는 조는 조문내용이 조 제목뿐이다. 항·호·목은 같은 응답의 "항"에 들어 있으므로
    JO= 재요청 없이 조문원문으로 평탄화한다.
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_OC = os.environ.get("LAW_OC", "")   # 환경변수로 지정. open.law.go.kr 에서 발급
BASE = "http://www.law.go.kr/DRF/"

# 검색결과 노드 키는 target 이름과 다를 수 있다(실측): ordin·eflaw→law, detc→Detc.
NODE_KEYS = {"law": "law", "eflaw": "law", "ordin": "law", "prec": "prec",
             "detc": "Detc", "expc": "expc", "admrul": "admrul"}
# 결과 노드를 찾을 때 건너뛸 응답 메타 필드.
META_KEYS = {"resultMsg", "resultCode", "target", "totalCnt", "page",
             "numOfRows", "section", "키워드", "이름"}


def _as_list(v):
    """DRF는 결과가 1건이면 배열 대신 객체를 준다 — 항상 list로 정규화."""
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _txt(v):
    """문자열이 아닐 수도 있는(None·list) 본문 필드를 안전하게 문자열로."""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return "\n".join(t for t in (_txt(x) for x in v) if t)
    if v is None or isinstance(v, dict):
        return ""
    return str(v).strip()


def _pick_results(wrap, target):
    """응답 래퍼에서 결과 노드를 꺼낸다(정규화 전 원값)."""
    for key in (NODE_KEYS.get(target), target):
        if key and key in wrap:
            return wrap[key]
    lowered = {k.lower(): v for k, v in wrap.items()}
    if target.lower() in lowered:            # detc→Detc 같은 대소문자 차이
        return lowered[target.lower()]
    return next((v for k, v in wrap.items()  # 미지의 target: 메타 아닌 첫 dict/list
                 if k not in META_KEYS and isinstance(v, (dict, list))), None)


def _fetch(endpoint, params):
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return url, r.read().decode("utf-8", "replace")


def _fail(msg):
    print(json.dumps({"error": msg,
                      "note": "법령검색 실패 — 조용히 통과 금지, 사용자에게 명시하고 대체(수동검색) 안내"},
                     ensure_ascii=False))
    sys.exit(3)


def _emit(a, out, empty):
    s = json.dumps(out, ensure_ascii=False, indent=2)
    print(s)
    if getattr(a, "out", None):
        try:
            open(a.out, "w", encoding="utf-8").write(s)
        except Exception:
            pass
    sys.exit(2 if empty else 0)


def cmd_search(a):
    params = {"OC": a.oc or DEFAULT_OC, "target": a.target, "type": "JSON",
              "query": a.query, "display": str(a.display), "page": str(a.page)}
    try:
        url, txt = _fetch("lawSearch.do", params)
        d = json.loads(txt)
    except Exception as e:
        _fail(f"{type(e).__name__}: {str(e)[:150]}")
    wrap = next((v for v in d.values() if isinstance(v, dict)), {})
    items = _as_list(_pick_results(wrap, a.target))
    total = wrap.get("totalCnt")
    # totalCnt가 0이 아닌데 결과를 못 뽑았다면 '결과없음'이 아니라 파싱 실패다.
    # 종료코드 2로 내보내면 사용자가 '해당 법령 없음'으로 오판하므로 오류로 올린다.
    if not items and str(total or "0").strip().isdigit() and int(total) > 0:
        _fail(f"응답 파싱 실패 — totalCnt={total}인데 결과 노드를 못 찾음 "
              f"(래퍼 키: {sorted(wrap)}). 결과없음 아님. url={url}")
    _emit(a, {"query": a.query, "target": a.target, "total": total,
              "request_url": url, "results": items}, empty=not items)


def _article(u):
    """조문단위 → 항·호·목까지 펼친 조문 하나.

    항이 있는 조는 조문내용이 제목뿐이므로(실측 605/605, 본문 중복 없음)
    항·호·목을 이어붙인 '조문원문'을 함께 준다. 인용은 이 필드를 쓴다.
    """
    hangs = []
    for h in _as_list(u.get("항")):
        if not isinstance(h, dict):
            continue
        hos = []
        for ho in _as_list(h.get("호")):
            if not isinstance(ho, dict):
                continue
            moks = [t for t in (_txt(m.get("목내용"))
                                for m in _as_list(ho.get("목")) if isinstance(m, dict)) if t]
            hos.append({"호번호": ho.get("호번호"), "호내용": _txt(ho.get("호내용")),
                        "목": moks})
        hangs.append({"항번호": h.get("항번호"), "항내용": _txt(h.get("항내용")),
                      "호": hos})
    head = _txt(u.get("조문내용"))
    lines = [head] if head else []
    for h in hangs:
        if h["항내용"]:
            lines.append(h["항내용"])
        for ho in h["호"]:
            if ho["호내용"]:
                lines.append("  " + ho["호내용"])
            lines.extend("    " + m for m in ho["목"])
    return {"조문번호": u.get("조문번호"), "조문가지번호": u.get("조문가지번호"),
            "조문제목": u.get("조문제목"), "조문여부": u.get("조문여부"),
            "조문내용": head, "조문원문": "\n".join(lines), "항": hangs}


def _jo_match(u, spec):
    """--jo 25 → 제25조(가지조항 제외), --jo 7의2 → 제7조의2.

    조문단위에는 장·절 제목(조문여부='전문')이 섞여 있고 뒤따르는 조의 조문번호를
    그대로 달고 있다 — 걸러내지 않으면 --jo 15가 '제3장 개인정보의 처리'를 준다.
    """
    if u.get("조문여부") == "전문":
        return False
    num, _, branch = str(spec).partition("의")
    return (str(u.get("조문번호")) == num.strip()
            and str(u.get("조문가지번호") or "") == branch.strip())


def _ordin_split(t):
    """자치법규 조내용은 항·호가 **줄바꿈 없이 통짜로** 온다.

    '…원칙으로 한다.1. 보건ㆍ사회: …2. 산업ㆍ경제: …' 처럼 붙어 나와 대비표·발췌서에
    그대로 실으면 읽을 수 없다. 경계가 분명한 항(②~)과 호(숫자.)만 나눈다.
    목(가.~)은 문장 끝 '…한다.' 와 구분되지 않아 건드리지 않는다.
    앞이 한글·괄호일 때만 나누므로 날짜('2025.1.20.')는 걸리지 않는다.
    """
    if not t:
        return t
    # 문장 끝('…있다.1. 재난')에서도 나뉘어야 해서 마침표 뒤까지 허용한다. 그러면
    # 날짜('2025.1.20.')가 쪼개지므로, 날짜의 점을 잠시 치환해 보호한 뒤 복원한다.
    t = re.sub(r"\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?",
               lambda m: m.group(0).replace(".", "\x00"), t)
    t = re.sub(r"(?<=[가-힣>\]\).])(\d{1,2}\.)(?=\s|[가-힣])", r"\n\1", t)
    t = t.replace("\x00", ".")
    t = re.sub(r"(?<=[^\n])([②-⑳])", r"\n\1", t)
    return t


def _ordin_article(u):
    """자치법규 조[] 원소 → 법령 조문단위와 같은 모양으로 정규화.

    자치법규 응답은 법령과 구조가 다르다: `LawService > 조문 > 조[]` 이고
    필드는 `조문번호`(['000100','000100'])·`조제목`·`조내용`·`조문여부`(Y/N)뿐이라
    항·호가 분리돼 있지 않다. 조번호는 인코딩을 믿지 말고 본문 머리에서 읽는다.
    """
    body = _ordin_split(_txt(u.get("조내용")))
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", body or "")
    return {"조문번호": m.group(1) if m else None,
            "조문가지번호": (m.group(2) if m and m.group(2) else ""),
            "조문제목": u.get("조제목"),
            "조문여부": "조문" if u.get("조문여부") == "Y" else "전문",
            "조문내용": body, "조문원문": body, "항": []}


def cmd_ordin(a):
    """자치법규(조례·규칙) 본문·조문 조회."""
    params = {"OC": a.oc or DEFAULT_OC, "target": "ordin", "type": "JSON"}
    if a.mst:
        params["MST"] = a.mst
    elif a.id:
        params["ID"] = a.id
    else:
        _fail("--mst 또는 --id 필요")
    try:
        url, txt = _fetch("lawService.do", params)
        d = json.loads(txt)
    except Exception as e:
        _fail(f"{type(e).__name__}: {str(e)[:150]}")
    b = d.get("LawService") or {}
    basic = b.get("자치법규기본정보", {})
    jo = b.get("조문") or {}
    units = _as_list(jo.get("조") if isinstance(jo, dict) else jo)
    arts = [_ordin_article(u) for u in units if isinstance(u, dict)]
    if a.jo:
        num, _, br = str(a.jo).partition("의")
        arts = [x for x in arts if x["조문여부"] != "전문"
                and x["조문번호"] == num.strip() and (x["조문가지번호"] or "") == br.strip()]
    _emit(a, {"자치법규명": basic.get("자치법규명"), "자치법규ID": basic.get("자치법규ID"),
              "지자체기관명": basic.get("지자체기관명"),
              "공포일자": basic.get("공포일자"), "시행일자": basic.get("시행일자"),
              "request_url": url, "조문수": len(arts), "조문": arts}, empty=not arts)


def cmd_law(a):
    if getattr(a, "target", "law") == "ordin":      # 자치법규는 응답 구조가 다르다
        return cmd_ordin(a)
    params = {"OC": a.oc or DEFAULT_OC, "target": "law", "type": "JSON"}
    if a.mst:
        params["MST"] = a.mst
    elif a.id:
        params["ID"] = a.id
    else:
        _fail("--mst 또는 --id 필요")
    try:
        url, txt = _fetch("lawService.do", params)
        d = json.loads(txt)
    except Exception as e:
        _fail(f"{type(e).__name__}: {str(e)[:150]}")
    law = d.get("법령", {})
    basic = law.get("기본정보", {})
    jo = law.get("조문", {})
    units = [u for u in _as_list(jo.get("조문단위") if isinstance(jo, dict) else None)
             if isinstance(u, dict)]
    if a.jo:
        units = [u for u in units if _jo_match(u, a.jo)]

    def dept(b):
        v = b.get("소관부처") or b.get("소관부처명")
        return v.get("content") if isinstance(v, dict) else v

    arts = [_article(u) for u in units]
    _emit(a, {"법령명": basic.get("법령명_한글"), "법령ID": basic.get("법령ID"),
              "공포일자": basic.get("공포일자"), "시행일자": basic.get("시행일자"),
              "소관부처": dept(basic), "request_url": url,
              "조문수": len(arts), "조문": arts}, empty=not arts)


def cmd_prec(a):
    params = {"OC": a.oc or DEFAULT_OC, "target": "prec", "type": "JSON", "ID": a.id}
    try:
        url, txt = _fetch("lawService.do", params)
        d = json.loads(txt)
    except Exception as e:
        _fail(f"{type(e).__name__}: {str(e)[:150]}")
    body = next((v for v in d.values() if isinstance(v, dict)), d)
    _emit(a, {"request_url": url, "판례": body}, empty=not body)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("search", "law", "prec"):
        s = sub.add_parser(name)
        s.add_argument("--oc", default=None)
        s.add_argument("--out", default=None)
        if name == "search":
            s.add_argument("--target", default="law")
            s.add_argument("--query", required=True)
            s.add_argument("--display", type=int, default=20)
            s.add_argument("--page", type=int, default=1)
        if name == "law":
            s.add_argument("--target", default="law", choices=["law", "ordin"],
                           help="ordin 이면 자치법규(조례·규칙) 본문을 조회")
            s.add_argument("--mst", default=None)
            s.add_argument("--id", default=None)
            s.add_argument("--jo", default=None)
        if name == "prec":
            s.add_argument("--id", required=True)
    a = ap.parse_args()
    if not (a.oc or DEFAULT_OC):
        # OC(기관코드)가 없으면 API가 빈 응답을 주고 파싱이 죽는다. 원인을 명확히 알린다.
        _fail("OC(기관코드)가 없습니다. 환경변수 LAW_OC 에 지정하거나 --oc 로 넘기세요. "
              "발급: https://open.law.go.kr/LSO/openApi/cuAskUsage.do (무료)")
    {"search": cmd_search, "law": cmd_law, "prec": cmd_prec}[a.cmd](a)


if __name__ == "__main__":
    main()
