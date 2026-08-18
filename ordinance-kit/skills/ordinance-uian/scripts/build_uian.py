# -*- coding: utf-8 -*-
"""조례안 완성본(의안 서식) HWPX 생성기 — 경기도의회 의원 발의 의안 규격.

내장 서식(assets/uian-template.hwpx = 실물 의안 완성본을 자리표시자로 비운 정본)의
문단을 **복제해서 글자만 갈아끼우는** 방식으로 만든다. 이 문서는 스타일 기반이
아니라 직접 서식 기반이라 스타일을 적용하는 방식으로는 서식이 재현되지 않는다.

원본 서식 결함(형제 항목의 문단모양이 제각각·호 문단 내어쓰기 0pt·주요내용
배분정렬)은 모델 문단 1개로 통일해 승계를 끊는다.

출력은 '스테이징' HWPX다 — 반드시 com_save.py로 한/글 정품 재저장을 거쳐야
보안수준 '높음'에서 열린다.

usage:
    python build_uian.py spec.json stage.hwpx [--template PATH]
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
NS = {"hp": HP}
Q = lambda n: "{%s}%s" % (HP, n)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TPL = os.path.join(HERE, "..", "assets", "uian-template.hwpx")

# 계층별 앞 공백 — 이 서식은 왼쪽여백이 아니라 '앞 공백 + 내어쓰기'로 계층을 만든다
INDENT = {"조": 0, "항": 2, "호": 2, "목": 4, "1)": 6, "가)": 8, "(1)": 10, "(가)": 12}

# ── 미검증 콘텐츠 가드 ──────────────────────────────────────────────
# spec.json 항목에 "[원문 미확인 ...]" / "[확인 필요 ...]" 류 마커가 있으면
# 그 항목은 완성본(의안 서식)에는 절대 남기지 않고 공란 처리한다. 원문은
# UNVERIFIED에 적재해 빌드 끝에 stdout으로만 보고한다(대화창 전달용).
UNVERIFIED_RE = re.compile(r"\[\s*(?:원문\s*미확인|미확인|확인\s*필요|검증\s*필요|미검증)[^\]]*\]")
UNVERIFIED = []


def is_unverified(text):
    return bool(text) and bool(UNVERIFIED_RE.search(text))


def guard(text, where):
    if is_unverified(text):
        UNVERIFIED.append("%s: %s" % (where, text.strip()))
        return ""
    return text

# 제○조(제목) 머리 — 여기까지만 진하게, 뒤 본문은 보통
RE_HEAD = re.compile(r"^(제\d+조(?:의\d+)?\s*\([^)]*\))(.*)$")

DEFAULT_NOTES = [
    "가. 관계법령: 덧붙임",
    "나. 입법예고: [○○의회 공고 제    -      호]",
    "   ○ 예고기간:     .    .    .(    ) ~     .    .    .(    )",
    "다. 비용추계서: (○○○과-    ,     .  .  .) ------------- [붙임 1]",
    "라. 조례안 검토의견: (○○○과-    ,     .  .  .) ----------- [붙임 2]",
    "마. 입법예고 결과: (○○○과-    ,     .  .  .) ----- [붙임 3]",
]
NOTE_PREFIXES = ("가. 관계법령", "나. 입법예고", "○ 예고기간", "다. 비용추계서",
                 "라. 조례안 검토의견", "마. 입법예고 결과")


# ---------------------------------------------------------------- 저수준 헬퍼
def drop_lsa(p):
    """레이아웃 캐시 제거 — 남기면 재저장 후 글자가 겹쳐 찍힌다."""
    for lsa in p.findall(Q("linesegarray")):
        p.remove(lsa)


def text_runs(p):
    """ctrl(secPr·pageNum 등)을 품지 않은 순수 텍스트 run."""
    return [r for r in p.findall(Q("run"))
            if r.find(Q("ctrl")) is None and r.find(Q("secPr")) is None]


def set_text(p, txt):
    """문단을 텍스트 하나로. ctrl을 품은 run은 보존하고 그 안의 hp:t만 교체한다.

    section1[0]에는 hp:secPr, section1[1]·section0[0]에는 hp:pageNum ctrl이 들어
    있다. 그 run을 지우면 구역설정·쪽번호가 통째로 날아간다.
    """
    target = None
    for r in p.findall(Q("run")):
        if r.find(Q("secPr")) is not None:      # 구역설정 run — 손대지 않음
            continue
        if r.find(Q("ctrl")) is not None:       # 쪽번호 등 — 여기에 텍스트를 넣는다
            target = r
            continue
        if target is None:
            target = r
        else:
            p.remove(r)
    if target is None:
        target = etree.SubElement(p, Q("run"))
        target.set("charPrIDRef", "32")
    for t in target.findall(Q("t")):
        target.remove(t)
    etree.SubElement(target, Q("t")).text = txt
    drop_lsa(p)
    return p


def clone(model, *texts):
    """모델 문단을 복제하고 텍스트 run에 순서대로 texts를 채운다.

    남는 run은 지운다 — 조문머리 모델은 (진하게 머리 + 보통 본문) 2개 run이라
    텍스트 1개만 주면 본문 run이 사라진다.
    """
    p = copy.deepcopy(model)
    drop_lsa(p)
    runs = text_runs(p)
    for r, tx in zip(runs, texts):
        for t in r.findall(Q("t")):
            r.remove(t)
        etree.SubElement(r, Q("t")).text = tx
    for r in runs[len(texts):]:
        p.remove(r)
    return p


def top_paras(root):
    """모든 hp:p (표 안 문단 포함, 문서 순서).

    의안 표지의 발의연월일·발의자는 표 셀 안 문단이라 최상위 자식만 훑으면
    잡히지 않는다 — 인덱스 맵은 이 평탄화 순번을 기준으로 한다.
    """
    return root.findall(".//hp:p", NS)


def replace_block(paras, first, last, texts, model):
    """paras[first..last]를 model 복제본 len(texts)개로 갈아끼운다(문단모양 통일)."""
    parent = paras[first].getparent()
    at = parent.index(paras[first])
    for p in paras[first:last + 1]:
        parent.remove(p)
    for k, txt in enumerate(texts):
        parent.insert(at + k, clone(model, txt))


# ---------------------------------------------------------------- 본문 조립
def body_items(entries, where="본문"):
    """[{level,text}] → 의안 서식 문단 명령 [('H',머리,본문) | ('S',텍스트) | ('B',)].
    미검증 마커가 있는 항목은 guard()가 빈 문자열로 바꾸고, 빈 항목은 기존 규칙대로
    빈 줄(B)로 처리된다 — 완성본에 반쪽 문장 대신 빈 줄만 남는다."""
    out = []
    for i, e in enumerate(entries):
        lv = e.get("level", "조")
        txt = guard((e.get("text") or "").strip(), where)
        if not txt:
            out.append(("B",))
            continue
        if lv == "조":
            if out and out[-1][0] != "B":       # 조 사이 빈 줄 1개
                out.append(("B",))
            m = RE_HEAD.match(txt)
            if m:
                out.append(("H", m.group(1), m.group(2)))
            else:
                out.append(("S", txt))
        else:
            out.append(("S", " " * INDENT.get(lv, 2) + txt))
    while out and out[0][0] == "B":
        out.pop(0)
    return out


def build(spec, tpl, stage):
    title = spec["조례명"]
    z = zipfile.ZipFile(tpl)
    order = z.namelist()
    items = {n: z.read(n) for n in order}
    z.close()

    # ═══════ section0 — 의안 표지 + 제안설명서 ═══════
    s0 = etree.fromstring(items["Contents/section0.xml"])
    ps = top_paras(s0)
    txt0 = ["".join(p.itertext()).strip() for p in ps]

    if "의원 대표발의" not in txt0[1]:
        raise SystemExit("서식이 예상과 다릅니다(문단1='%s'). 템플릿을 확인하세요." % txt0[1])

    set_text(ps[0], title)                                   # 제명
    set_text(ps[1], "(%s 의원 대표발의)" % spec["대표발의"])
    set_text(ps[9], spec.get("발의연월일", "    년    월    일"))
    set_text(ps[12], spec.get("발의자", spec["대표발의"]))

    # 서식 정본의 자리표시자가 남지 않도록, 값이 없어도 반드시 덮어쓴다
    warn = []

    # 1. 제안이유 — 모델 ps[17](paraPr 34)로 통일, 항목 수만큼 증감
    reasons = [" " + guard(r.strip(), "제안이유") for r in spec.get("제안이유", []) if r.strip()]
    reasons = [r for r in reasons if r.strip()]
    if not reasons:
        reasons, _ = [" (제안이유를 여기에 적습니다)"], warn.append("제안이유")
    replace_block(ps, 17, 18, reasons, ps[17])

    # 2. 주요내용 — 원본은 형제끼리 문단모양 4종. 모델 ps[23](paraPr 45)로 통일
    mains = [" " + guard(m.strip(), "주요내용") for m in spec.get("주요내용", []) if m.strip()]
    mains = [m for m in mains if m.strip()]
    if not mains:
        mains, _ = [" 가. (주요내용을 여기에 적습니다)"], warn.append("주요내용")
    if mains:
        ps2 = top_paras(s0)
        base = next(i for i, p in enumerate(ps2)
                    if "".join(p.itertext()).strip().startswith("2. 주요내용"))
        idxs = [i for i in range(base + 1, len(ps2))
                if re.match(r"^[가-힣]\.", "".join(ps2[i].itertext()).strip())]
        idxs = [i for i in idxs if i - idxs[0] == idxs.index(i)]   # 연속 구간만
        model = ps2[idxs[1]] if len(idxs) > 1 else ps2[idxs[0]]    # 나. 문단모양(45)
        replace_block(ps2, idxs[0], idxs[-1], mains, model)

    # 3. 참고사항 — 원본 다./라.는 배분정렬(줄이 짧으면 글자가 눌린다). 가.(48)로 통일
    notes = [n.strip() for n in spec.get("참고사항") or DEFAULT_NOTES if n.strip()]
    notes = [("   " + n) if n.startswith("○") else n for n in notes]
    ps3 = top_paras(s0)
    nidx = [i for i, p in enumerate(ps3)
            if "".join(p.itertext()).strip().startswith(NOTE_PREFIXES)]
    if nidx:
        replace_block(ps3, nidx[0], nidx[-1], notes, ps3[nidx[0]])

    items["Contents/section0.xml"] = etree.tostring(
        s0, encoding="UTF-8", xml_declaration=True, standalone=True)

    # ═══════ section1 — 조례안 본문 ═══════
    s1 = etree.fromstring(items["Contents/section1.xml"])
    ps = top_paras(s1)

    set_text(ps[0], "%s 조례  제   호" % spec.get("공포기관", "경기도"))
    set_text(ps[1], title)

    M_BLANK, M_HEAD, M_SUB = ps[4], ps[5], ps[6]
    supp_i = next(i for i, p in enumerate(ps)
                  if re.fullmatch(r"부\s*칙", "".join(p.itertext()).strip()))
    M_SUPT, M_SUPB = ps[supp_i], ps[supp_i + 1]

    parent = ps[3].getparent()
    tail = ps[supp_i + 3]                    # 본문 뒤 꼬리 문단(구역 마감)
    for p in ps[3:supp_i + 3]:
        parent.remove(p)

    def emit(cmds, at):
        for c in cmds:
            if c[0] == "H":
                np = clone(M_HEAD, c[1], c[2])
            elif c[0] == "S":
                np = clone(M_SUB, c[1])
            else:
                np = clone(M_BLANK)
            parent.insert(at, np)
            at += 1
        return at

    at = parent.index(tail)
    at = emit(body_items(spec["본칙"], where="본칙"), at)

    supp = spec.get("부칙") or [{"level": "조", "text": "이 조례는 공포한 날부터 시행한다."}]
    head = clone(M_SUPT, "부      칙")
    head.set("pageBreak", "1")               # 부칙이 앞 조문과 쪽이 갈리지 않도록
    for np in (head, clone(M_SUPB)):
        parent.insert(at, np)
        at += 1
    emit(body_items(supp, where="부칙"), at)

    items["Contents/section1.xml"] = etree.tostring(
        s1, encoding="UTF-8", xml_declaration=True, standalone=True)

    # ═══════ 검증 — 조문 번호가 1부터 빠짐없이 이어지는가 ═══════
    body = ["".join(p.itertext()).strip()
            for p in top_paras(etree.fromstring(items["Contents/section1.xml"]))]
    nums = [int(m.group(1)) for t in body for m in [re.match(r"제(\d+)조\(", t)] if m]
    want = [int(m.group(1)) for e in spec["본칙"]
            if not is_unverified((e.get("text") or "").strip())
            for m in [re.match(r"제(\d+)조\(", (e.get("text") or "").strip())] if m]
    if nums[:len(want)] != want:
        raise SystemExit("조문 번호 불일치\n 생성:%s\n 입력:%s" % (nums[:len(want)], want))
    whole = "".join(etree.fromstring(items["Contents/section0.xml"]).itertext()) + "".join(body)
    for ex in ("○○○○",):                       # 서식 정본 자리표시자 잔존 검사
        if ex in whole and ex not in json.dumps(spec, ensure_ascii=False):
            raise SystemExit("서식 정본의 자리표시자가 남았습니다: %r" % ex)
    if UNVERIFIED_RE.search(whole):
        raise SystemExit("미검증 마커가 완성본에 남았습니다 — guard() 누락 지점을 찾아 고칠 것")

    if os.path.exists(stage):
        os.remove(stage)
    zo = zipfile.ZipFile(stage, "w", zipfile.ZIP_DEFLATED)
    zi = zipfile.ZipInfo("mimetype")
    zi.compress_type = zipfile.ZIP_STORED        # mimetype은 무압축·선두 고정
    zo.writestr(zi, items["mimetype"])
    for n in order:
        if n != "mimetype":
            zo.writestr(n, items[n])
    zo.close()
    print("stage: %s (%d bytes, 조문 %d개)" % (stage, os.path.getsize(stage), len(want)))
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
    ap.add_argument("--template", default=DEFAULT_TPL)
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        build(json.load(f), a.template, a.stage)
