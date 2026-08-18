# -*- coding: utf-8 -*-
"""입법예고 의뢰서 HWPX 생성기 (경기도의회 법제과 2025-08-12 배포 서식).

제정조례용·개정조례용 2종 서식의 문단을 **복제해서 글자만 갈아끼우는** 방식으로
채운다. 서식 규격(A4·여백·글꼴·문단모양)은 변경금지 항목이라 손대지 않는다.

자동 처리:
  - 빨간 안내문 제거 — `(경기천년…)` `(조 사이 1줄 띄움)` 은 삭제, `∨` 는 공백
    한 칸으로. 빨간색이지만 실제 문장인 글자('조례안'·'의정지원과'·'법제과' 등)는
    지우지 않고 검정(charPr 1→203, 806→186)으로 되돌린다.
  - 「작성 시 주의사항」·「전부개정 시」 안내 박스 제거(제정 2개·개정 4문단)
  - 조문 수에 맞춰 조·항·호 문단 증감, 조 사이 빈 줄 1개 유지
  - (개정) 신·구조문 대비표 행 증감 + rowAddr·rowCnt 재계산

출력은 '스테이징' HWPX다 — 반드시 com_save.py로 한/글 정품 재저장을 거쳐야
보안수준 '높음'에서 열린다.

usage:
    python build_yego.py spec.json stage.hwpx [--type 제정|개정]
"""
import argparse
import copy
import json
import os
import re
import sys
import zipfile

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
Q = lambda n: "{%s}%s" % (HP, n)

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
TPL = {"제정": os.path.join(ASSETS, "250812-ipbeop-yego-jejeong.hwpx"),
       "개정": os.path.join(ASSETS, "250812-ipbeop-yego-gaejeong.hwpx")}

RED = {"1": "203", "806": "186"}                       # 빨강 → 검정 글자모양 대응
GUIDE = re.compile(r"\(경기천년[^)]*\)|\(조 사이 1줄 띄움\)|\(\s*스타일[^)]*\)")
INDENT = {"조": 0, "항": 2, "호": 2, "목": 4, "1)": 6, "가)": 8, "(1)": 10, "(가)": 12}

# ── 미검증 콘텐츠 가드 ──────────────────────────────────────────────
# spec.json 필드에 "[원문 미확인 ...]" / "[확인 필요 ...]" 류 마커가 섞여 있으면
# 그 항목은 통째로 공란 처리해 제출용 HWPX에는 절대 남기지 않는다. 마커의 원문은
# UNVERIFIED에 적재해 두었다가 빌드 끝에 stdout으로만 보고한다(대화창 전달용,
# 문서에는 절대 쓰지 않는다). 값이 없어 자리표시자로 채운 기존 `warn` 로직과는
# 별개다 — `warn`은 "입력이 아예 없었다", `UNVERIFIED`는 "입력은 있었으나 원문을
# 확인하지 못했다"를 뜻한다.
UNVERIFIED_RE = re.compile(r"\[\s*(?:원문\s*미확인|미확인|확인\s*필요|검증\s*필요|미검증)[^\]]*\]")
UNVERIFIED = []


def guard(text, where):
    """마커가 있으면 완성본에는 빈 문자열을 돌려주고 원문을 UNVERIFIED에 적재한다."""
    if text and UNVERIFIED_RE.search(text):
        UNVERIFIED.append("%s: %s" % (where, text.strip()))
        return ""
    return text

NOTICE = (" {의회} {위원회} {의원} 의원이 추진 중인 「{조례명}」을 {의회} "
          "법제사무처리 지침에 따라 그 취지와 내용을 미리 알려 주민 여러분의 의견을 "
          "듣고자 다음과 같이 입법예고 합니다.")
DEFAULT_COUNCIL = "경기도의회"          # 서식 배포 기관. 타 의회는 spec의 `의회명`으로 바꾼다

# 문단 인덱스 맵 — references/template-spec*.md 의 실측값
MAP = {
    "제정": dict(
        공고번호=0, 예고문=2, 공고일=3, 제목행=6,
        이유=(9, 11), 내용=(14, 16), 문의처=21, 제출기한=24,
        박스=[29, 58], 공포번호=30, 본문제목=32, 조문=(34, 53),
        부칙=(57, 57), 법령=(62, 66),
        M=dict(이유=9, 내용=14, 조=34, 호=40, 항=49, 빈=37, 부칙문=57,
               법령명=62, 법령빈=63, 발췌조=64, 발췌항=65),
    ),
    "개정": dict(
        공고번호=0, 예고문=2, 공고일=3, 제목행=6,
        이유=(9, 11), 내용=(14, 15), 대비표항목=19, 문의처=22, 제출기한=25,
        박스=[30, 31, 32, 100], 공포번호=33, 본문제목=35, 개정두문=37,
        조문=(39, 48), 부칙=(52, 52), 대비표제목=53, 대비표=54,
        현행조례명=57, 현행조례=(59, 98), 법령=(103, 120),
        M=dict(이유=9, 내용=14, 조=39, 호=62, 항=46, 빈=38, 부칙문=52,
               법령명=103, 법령빈=104, 발췌조=105, 발췌항=108),
    ),
}


# ---------------------------------------------------------------- 저수준 헬퍼
def drop_lsa(el):
    for p in el.iter(Q("p")):
        for lsa in p.findall(Q("linesegarray")):
            p.remove(lsa)


def top_paras(root):
    return [c for c in root if c.tag == Q("p")]


def run_text(r):
    return "".join("".join(t.itertext()) for t in r.findall(Q("t")))


def clean_red(el):
    """빨간 안내문만 지우고, 실제 문장인 빨간 글자는 검정으로 되돌린다."""
    for p in el.iter(Q("p")):
        touched = False
        for r in list(p.findall(Q("run"))):
            cid = r.get("charPrIDRef")
            if cid not in RED:
                continue
            txt = GUIDE.sub("", run_text(r)).replace("∨", " ")
            if txt.strip() == "" and r.find(Q("ctrl")) is None and r.find(Q("secPr")) is None:
                p.remove(r)                      # 안내문뿐인 run — 통째로 제거
                touched = True
                continue
            for t in r.findall(Q("t")):
                r.remove(t)
            etree.SubElement(r, Q("t")).text = txt
            r.set("charPrIDRef", RED[cid])       # 빨강 → 검정
            touched = True
        if touched:
            for lsa in p.findall(Q("linesegarray")):
                p.remove(lsa)


def strip_model(p):
    """모델 문단에서 빨간 run을 걷어내 텍스트 run 하나만 남긴다."""
    m = copy.deepcopy(p)
    for r in list(m.findall(Q("run"))):
        if r.get("charPrIDRef") in RED:
            m.remove(r)
    drop_lsa(m)
    return m


def set_text(p, txt):
    """문단을 텍스트 하나로. ctrl(쪽번호 등)을 품은 run은 보존하고 hp:t만 교체."""
    target = None
    for r in p.findall(Q("run")):
        if r.find(Q("secPr")) is not None:
            continue
        if r.find(Q("ctrl")) is not None:
            target = r
            continue
        if target is None:
            target = r
        else:
            p.remove(r)
    if target is None:
        target = etree.SubElement(p, Q("run"))
        target.set("charPrIDRef", "186")
    if target.get("charPrIDRef") in RED:
        target.set("charPrIDRef", RED[target.get("charPrIDRef")])
    for t in target.findall(Q("t")):
        target.remove(t)
    etree.SubElement(target, Q("t")).text = txt
    for lsa in p.findall(Q("linesegarray")):
        p.remove(lsa)


def clone(model, txt=""):
    p = copy.deepcopy(model)
    drop_lsa(p)
    set_text(p, txt)
    return p


def replace_block(ps, first, last, texts, model):
    """ps[first..last] 를 model 복제본 len(texts)개로 교체(문단모양 통일)."""
    parent = ps[first].getparent()
    at = parent.index(ps[first])
    for p in ps[first:last + 1]:
        parent.remove(p)
    for k, txt in enumerate(texts):
        parent.insert(at + k, clone(model, txt))


def emit_body(entries, M, where="본문"):
    """[{level,text}] → 조문 문단 목록 (조 사이 빈 줄 1개). 미검증 마커가 있는
    항목은 guard()가 빈 문자열로 바꾸고, 빈 항목은 기존 규칙(공백 항목 스킵)에
    따라 그대로 제외된다 — 미완성 문장을 반쪽으로 문서에 남기지 않는다."""
    out, prev = [], None
    for e in entries:
        lv = e.get("level", "조")
        txt = guard((e.get("text") or "").strip(), where)
        if not txt:
            continue
        if lv == "조" and prev is not None:
            out.append(clone(M["빈"]))
        model = M["조"] if lv == "조" else (M["항"] if lv == "항" else M["호"])
        out.append(clone(model, " " * INDENT.get(lv, 2) + txt))
        prev = lv
    return out


# ---------------------------------------------------------------- 대비표
def fill_table(p, pairs):
    """신·구조문 대비표: 머리행·마감행은 두고 데이터행만 pairs 수에 맞춘다."""
    tbl = p.find(".//" + Q("tbl"))
    trs = tbl.findall(Q("tr"))
    head, first, model, closing = trs[0], trs[1], trs[2], trs[-1]

    def put(tr, left, right):
        left = guard(str(left), "신구조문대비표(현행)")
        right = guard(str(right), "신구조문대비표(개정안)")
        for tc, txt in zip(tr.findall(Q("tc")), (left, right)):
            sub = tc.find(Q("subList"))
            base = sub.findall(Q("p"))[0]
            lines = str(txt).split("\n")
            for old in sub.findall(Q("p"))[1:]:
                sub.remove(old)
            set_text(base, lines[0])
            at = list(sub).index(base)
            for k, ln in enumerate(lines[1:], 1):
                sub.insert(at + k, clone(base, ln))
        drop_lsa(tr)
        return tr

    rows = [head]
    for i, (l, r) in enumerate(pairs):
        tr = copy.deepcopy(model)
        if i == 0:      # 첫 행은 머리행과 맞물리는 테두리(borderFill)를 따로 쓴다.
            for tc, src in zip(tr.findall(Q("tc")), first.findall(Q("tc"))):
                tc.set("borderFillIDRef", src.get("borderFillIDRef"))
        rows.append(put(tr, l, r))
    rows.append(closing)

    for tr in trs:
        tbl.remove(tr)
    for ri, tr in enumerate(rows):
        for tc in tr.findall(Q("tc")):
            tc.find(Q("cellAddr")).set("rowAddr", str(ri))
        tbl.append(tr)
    tbl.set("rowCnt", str(len(rows)))
    drop_lsa(p)


# ---------------------------------------------------------------- 빌드
def build(spec, kind, stage):
    tpl = TPL[kind]
    z = zipfile.ZipFile(tpl)
    order = z.namelist()
    items = {n: z.read(n) for n in order}
    z.close()

    root = etree.fromstring(items["Contents/section0.xml"])
    ps = top_paras(root)
    m = MAP[kind]
    M = {k: strip_model(ps[i]) for k, i in m["M"].items()}

    title = spec["조례명"]
    org = spec.get("공포기관", "경기도")
    gaejeong = kind == "개정"

    council = spec.get("의회명") or DEFAULT_COUNCIL
    set_text(ps[m["공고번호"]], "%s 공고 %s" % (council, spec.get("공고번호", "제    -    호")))
    set_text(ps[m["예고문"]], NOTICE.format(의회=council,
                                            위원회=spec.get("소관위원회", "○○○위원회"),
                                            의원=spec.get("대표발의", "○○○"),
                                            조례명=title))
    set_text(ps[m["공고일"]], spec.get("공고일", "    .   .   ."))
    set_text(ps[m["제목행"]], "「%s」" % title)
    set_text(ps[m["공포번호"]], "%s 조례 제    호" % org)
    set_text(ps[m["본문제목"]], "「%s」" % title)
    set_text(ps[m["문의처"]], " ○ %s %s" % (council, spec.get("문의처") or "○○○과 000)0000-0000"))
    set_text(ps[m["제출기한"]], " 가. 제출기한 : %s" % spec.get("제출기한", "    년   월   일(  )까지"))

    # 서식 정본의 자리표시자가 제출본에 남지 않도록, 값이 없으면
    # 비어 있음이 눈에 보이는 자리표시자로 반드시 덮어쓴다.
    warn = []
    reasons = [" " + guard(r.strip(), "제안이유") for r in spec.get("제안이유", []) if r.strip()]
    reasons = [r for r in reasons if r.strip()]
    if not reasons:
        reasons, _ = [" 가. (제정이유를 여기에 적습니다)"], warn.append("제안이유")
    replace_block(ps, m["이유"][0], m["이유"][1], reasons, M["이유"])
    mains = [" " + guard(t.strip(), "주요내용") for t in spec.get("주요내용", []) if t.strip()]
    mains = [t for t in mains if t.strip()]
    if not mains:
        mains, _ = [" 가. (주요내용을 여기에 적습니다)"], warn.append("주요내용")
    replace_block(ps, m["내용"][0], m["내용"][1], mains, M["내용"])

    # ── 조례안 본문 ────────────────────────────────────────────────
    if gaejeong:
        set_text(ps[m["개정두문"]],
                 "「%s」 일부를 다음과 같이 개정한다." % spec.get("현행조례명", title))
        body = spec.get("개정문") or spec.get("본칙")
    else:
        body = spec["본칙"]
    parent = ps[m["조문"][0]].getparent()
    at = parent.index(ps[m["조문"][0]])
    for p in ps[m["조문"][0]:m["조문"][1] + 1]:
        parent.remove(p)
    for k, np in enumerate(emit_body(body, M)):
        parent.insert(at + k, np)

    supp = spec.get("부칙") or [{"level": "조", "text": "이 조례는 공포한 날부터 시행한다."}]
    supp_lines = []
    for e in supp:                                   # 조가 여럿이면 사이에 빈 줄 1개
        txt = guard((e.get("text") or "").strip(), "부칙")
        if not txt:
            continue
        if supp_lines and e.get("level", "조") == "조":
            supp_lines.append("")
        supp_lines.append(txt)
    if not supp_lines:
        supp_lines = ["이 조례는 공포한 날부터 시행한다."]
    replace_block(ps, m["부칙"][0], m["부칙"][1], supp_lines, M["부칙문"])

    # ── (개정) 신·구조문 대비표 / 참고 1 현행 조례 ──────────────────
    if gaejeong:
        pairs = spec.get("대비표") or []
        if not pairs:
            pairs = [["(현행 조문)", "(개정안 조문)"]]
            warn.append("신·구조문 대비표")
        fill_table(ps[m["대비표"]], pairs)
        if spec.get("전부개정") or spec.get("유형") == "전부개정":
            set_text(ps[m["대비표항목"]], "4. 주요조문 대비표 : 붙임")
            set_text(ps[m["대비표제목"]], "주요조문 대비표")
        cur = spec.get("현행조례") or []
        if not cur:
            cur = [{"level": "조", "text": "(현행 조례 전문을 여기에 넣습니다 — law-search로 조회)"}]
            warn.append("현행 조례 전문")
        if cur:
            set_text(ps[m["현행조례명"]], "「%s」" % spec.get("현행조례명", ""))
            parent = ps[m["현행조례"][0]].getparent()
            at = parent.index(ps[m["현행조례"][0]])
            for p in ps[m["현행조례"][0]:m["현행조례"][1] + 1]:
                parent.remove(p)
            for k, np in enumerate(emit_body(cur, M, where="현행조례")):
                parent.insert(at + k, np)

    # ── 관계 법령 발췌서 ───────────────────────────────────────────
    laws = spec.get("관계법령") or []
    if not laws:
        laws = [{"법령명": "○○○법", "조문": [{"level": "조",
                 "text": "(상위법 근거 조문을 여기에 발췌해 넣습니다)"}]}]
        warn.append("관계법령 발췌")
    if laws:
        out = []
        for i, law in enumerate(laws):
            law_name = guard((law.get("법령명") or "").strip(), "관계법령(법령명)")
            arts = []
            for e in law.get("조문", []):
                lv = e.get("level", "조")
                txt = guard((e.get("text") or "").strip(), "관계법령(%s)" % (law.get("법령명") or "?"))
                if not txt:
                    continue
                model = M["발췌조"] if lv == "조" else M["발췌항"]
                arts.append(clone(model, " " * INDENT.get(lv, 2) + txt))
            if not law_name or not arts:
                # 법령명 또는 조문 전체가 미검증이면 이 법령 블록째 제외한다
                # (헤더만 남고 본문이 비는 어색한 모양을 피한다). guard()가 개별
                # 사유는 이미 UNVERIFIED에 적재했으므로 여기서는 요약만 남긴다.
                UNVERIFIED.append("관계법령(%s): 검증된 조문이 없어 블록 전체 제외"
                                  % (law.get("법령명") or "법령명 미확인"))
                continue
            if out:
                out.append(clone(M["법령빈"]))
            out.append(clone(M["법령명"], "□ " + law_name))
            out.append(clone(M["법령빈"]))
            out.extend(arts)
        parent = ps[m["법령"][0]].getparent()
        at = parent.index(ps[m["법령"][0]])
        for p in ps[m["법령"][0]:m["법령"][1] + 1]:
            parent.remove(p)
        for k, np in enumerate(out):
            parent.insert(at + k, np)

    # ── 안내 박스 제거 · 빨간 안내문 정리 ──────────────────────────
    for i in m["박스"]:
        ps[i].getparent().remove(ps[i])
    clean_red(root)

    items["Contents/section0.xml"] = etree.tostring(
        root, encoding="UTF-8", xml_declaration=True, standalone=True)

    # ── 검증 ──────────────────────────────────────────────────────
    chk = etree.fromstring(items["Contents/section0.xml"])
    txt = "".join(chk.itertext())
    for bad in ("경기천년제목 Medium, 10", "경기천년바탕 Regular, 10", "조 사이 1줄 띄움", "∨"):
        if bad in txt:
            raise SystemExit("안내문이 남았습니다: %r" % bad)
    if "작성 시 주의사항" in txt:
        raise SystemExit("안내 박스가 남았습니다")
    if title not in txt:
        raise SystemExit("조례명이 반영되지 않았습니다")
    # 서식 정본의 자리표시자가 제출본에 남으면 안 된다(문단 인덱스가 틀렸다는 신호)
    for ex in ("○○○○에 관하여 필요한 사항을 규정한다", "○○○○에 관한 사항"):
        if ex in txt and ex not in json.dumps(spec, ensure_ascii=False):
            raise SystemExit("서식 정본의 자리표시자가 남았습니다: %r" % ex)
    # 미검증 마커가 실수로라도 문서에 남지 않았는지 최종 방어선
    if UNVERIFIED_RE.search(txt):
        raise SystemExit("미검증 마커가 완성본에 남았습니다 — guard() 누락 지점을 찾아 고칠 것")

    if os.path.exists(stage):
        os.remove(stage)
    zo = zipfile.ZipFile(stage, "w", zipfile.ZIP_DEFLATED)
    zi = zipfile.ZipInfo("mimetype")
    zi.compress_type = zipfile.ZIP_STORED
    zo.writestr(zi, items["mimetype"])
    for n in order:
        if n != "mimetype":
            zo.writestr(n, items[n])
    zo.close()
    print("stage: %s (%d bytes, %s)" % (stage, os.path.getsize(stage), kind))
    if warn:
        print("자리표시자로 채운 항목(제출 전 반드시 채울 것): %s" % ", ".join(warn))
    if UNVERIFIED:
        print("[미검증 — 완성본에서 공란 처리함, 대화창으로만 보고할 것] %d건" % len(UNVERIFIED))
        for note in UNVERIFIED:
            print("  - %s" % note)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("stage")
    ap.add_argument("--type", dest="kind", choices=["제정", "개정"])
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        s = json.load(f)
    kind = a.kind or ("개정" if "개정" in s.get("유형", "제정") else "제정")
    build(s, kind, a.stage)
