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
  - (개정) 대비표 셀의 `〔U〕…〔/U〕` 밑줄 표식 → 밑줄 run으로 분할(표식 문자는 제거)

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
INDENT = {"장": 0, "절": 0, "조": 0, "항": 2, "호": 2, "목": 4, "1)": 6, "가)": 8, "(1)": 10, "(가)": 12}

# ── 신·구조문 대비표 밑줄 표식 ─────────────────────────────────────
# suite.json 대비표 셀은 바뀐 자구를 `〔U〕…〔/U〕`(U+3014/U+3015)로 감싼다
# (ordinance-suite spec-schema). 표식 안쪽만 밑줄 charPr로 run을 쪼개고 표식
# 문자는 제거한다. 밑줄 charPr은 셀 기본 charPr(32)을 복제해 underline BOTTOM으로
# 바꾼 것을 **charProperties 목록 맨 끝에** 덧붙인다 — 중간에 끼우면 한/글 COM
# 재저장 때 charPr id가 위치 순으로 재부여돼 뒤 id가 전부 밀린다(밑줄 run이
# 빨간 글자(1·806)로 바뀌는 사고가 실제로 있었다).
UL_OPEN, UL_CLOSE = "〔U〕", "〔/U〕"          # 〔U〕 〔/U〕
UL_SPLIT = re.compile("(%s.*?%s)" % (re.escape(UL_OPEN), re.escape(UL_CLOSE)))
UL_ANY = re.compile("%s|%s" % (re.escape(UL_OPEN), re.escape(UL_CLOSE)))
CELL_CHARPR = "32"                                     # 대비표 데이터행 셀의 기본 글자모양


def add_underline_charpr(hdr, base=CELL_CHARPR):
    """header.xml(str)에 base charPr 복제+밑줄 BOTTOM charPr을 목록 끝에 추가.
    (수정된 header, 새 id) 반환. 헤더는 문자열 치환만 해 나머지 바이트를 보존한다."""
    m = re.search(r'<hh:charPr id="%s".*?</hh:charPr>' % base, hdr, flags=re.S)
    if not m:
        raise SystemExit("header.xml에 charPr %s 가 없습니다" % base)
    cnt_m = re.search(r'<hh:charProperties itemCnt="(\d+)"', hdr)
    cnt = int(cnt_m.group(1))
    ids = re.findall(r'<hh:charPr id="(\d+)"', hdr)
    if len(ids) != cnt or ids[-1] != str(cnt - 1):
        raise SystemExit("charProperties itemCnt(%d)와 실제 charPr 목록(%d, last=%s)이 다릅니다"
                         % (cnt, len(ids), ids[-1] if ids else None))
    new_id = str(cnt)
    new_cp = m.group(0).replace('id="%s"' % base, 'id="%s"' % new_id, 1)
    new_cp, n = re.subn(r'<hh:underline type="[A-Z_]+"', '<hh:underline type="BOTTOM"', new_cp, count=1)
    if n != 1 or 'type="BOTTOM"' not in new_cp:
        raise SystemExit("charPr %s 에 underline 요소가 없어 밑줄 charPr을 만들 수 없습니다" % base)
    hdr = hdr.replace("</hh:charProperties>", new_cp + "</hh:charProperties>", 1)
    hdr = hdr.replace('<hh:charProperties itemCnt="%d"' % cnt,
                      '<hh:charProperties itemCnt="%d"' % (cnt + 1), 1)
    return hdr, new_id


def has_ul_marker(obj):
    return UL_OPEN in json.dumps(obj, ensure_ascii=False)

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

# ── 유형 판별 — ordinance-suite/scripts/ordtype.py 의 **동일 사본** ──────────
# (스킬 단독 동작을 위해 인라인. 규칙을 바꾸면 두 곳을 같이 고칠 것)
TYPES = ("제정", "일부개정", "전부개정")


class TypeConflict(ValueError):
    pass


def type_from_title(title):
    t = re.sub(r"\s+", "", title or "")
    if "전부개정" in t:
        return "전부개정"
    if "일부개정" in t or t.endswith(("개정조례안", "개정규칙안")):
        return "일부개정"
    return "제정"


def detect_type(title, explicit=None):
    derived = type_from_title(title)
    if explicit in (None, ""):
        return derived
    exp = re.sub(r"\s+", "", str(explicit))
    if exp not in TYPES:
        raise TypeConflict("알 수 없는 유형 %r — %s 중 하나여야 합니다" % (explicit, "·".join(TYPES)))
    if derived != "제정" and derived != exp:
        raise TypeConflict("유형 충돌: 제명 「%s」은 %s인데 spec 유형은 %s입니다 — 제명 또는 유형을 고치십시오"
                           % (title, derived, exp))
    return exp


RE_DUMUN = re.compile(r"^\s*「?\s*(.+?)\s*」?\s*(일부|전부)\s*를\s*다음과\s*같이\s*개정한다\.?\s*$")
PH_CONTACT = "○○○과 031)8008-○○○○"


def current_name(spec):
    """현행조례명 — 없으면 제명에서 '일부개정조례안' 등을 떼어 추정한다."""
    v = re.sub(r"[「」]", "", str(spec.get("현행조례명") or "")).strip()
    if v:
        return v
    return re.sub(r"\s*(?:일부\s*개정|전부\s*개정|개정)\s*(?:조례|규칙)안?\s*$", "",
                  spec.get("조례명", "")).strip()


def dumun(spec, 유형, warn=None):
    """개정 두문 — 일부개정: 「…」 일부를 …, 전부개정: 「…」 전부를 다음과 같이 개정한다."""
    name = current_name(spec)
    if not str(spec.get("현행조례명") or "").strip() and warn is not None:
        warn.append("현행조례명(제명에서 추정: %s)" % name)
    return "「%s」 %s를 다음과 같이 개정한다." % (name, "전부" if 유형 == "전부개정" else "일부")


NOTICE = (" 경기도의회 {위원회} {의원} 의원이 추진 중인 「{조례명}」을 경기도의회 "
          "법제사무처리 지침에 따라 그 취지와 내용을 미리 알려 도민 여러분의 의견을 "
          "듣고자 다음과 같이 입법예고 합니다.")

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


def set_runs(p, txt, ul_id):
    """문단을 텍스트 하나로 채우되 `〔U〕…〔/U〕` 안쪽은 밑줄 charPr(ul_id) run으로,
    바깥은 기본 charPr run으로 분할해 넣는다. 표식 문자는 제거한다. ul_id가 None이면
    set_text와 같다(표식은 그대로 남아 최종 가드에 걸린다)."""
    if not ul_id or UL_OPEN not in txt:
        set_text(p, txt)
        return
    set_text(p, "")                                   # run 정리(ctrl 보존)는 set_text에 맡긴다
    target = None
    for r in p.findall(Q("run")):
        if r.find(Q("secPr")) is None and r.find(Q("ctrl")) is None:
            target = r
    if target is None:                                # ctrl run뿐이면 그 run을 기준으로 삼는다
        target = [r for r in p.findall(Q("run")) if r.find(Q("secPr")) is None][-1]
    base_id = target.get("charPrIDRef")
    segs = []
    for seg in UL_SPLIT.split(txt):
        if not seg:
            continue
        if seg.startswith(UL_OPEN) and seg.endswith(UL_CLOSE):
            inner = seg[len(UL_OPEN):-len(UL_CLOSE)]
            if inner:
                segs.append((inner, ul_id))
        else:
            segs.append((seg, base_id))
    at = p.index(target)
    if target.find(Q("ctrl")) is None:
        p.remove(target)
        at -= 0
    else:
        at += 1                                       # ctrl run은 두고 그 뒤에 이어 붙인다
    for k, (text, cid) in enumerate(segs):
        r = etree.Element(Q("run"))
        r.set("charPrIDRef", cid)
        etree.SubElement(r, Q("t")).text = text
        p.insert(at + k, r)
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
        if lv in ("조", "장", "절") and prev is not None:
            out.append(clone(M["빈"]))
        model = M["조"] if lv in ("조", "장", "절") else (M["항"] if lv == "항" else M["호"])
        out.append(clone(model, " " * INDENT.get(lv, 2) + txt))
        prev = lv
    return out


# ---------------------------------------------------------------- 대비표
def fill_table(p, pairs, ul_id=None):
    """신·구조문 대비표: 머리행·마감행은 두고 데이터행만 pairs 수에 맞춘다.
    ul_id가 있으면 셀의 `〔U〕…〔/U〕` 표식을 밑줄 run으로 바꾼다(set_runs)."""
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
            model_p = copy.deepcopy(base)         # 분할 전 원형(기본 charPr run 1개)
            drop_lsa(model_p)
            set_runs(base, lines[0], ul_id)
            at = list(sub).index(base)
            for k, ln in enumerate(lines[1:], 1):
                np = copy.deepcopy(model_p)
                set_runs(np, ln, ul_id)
                sub.insert(at + k, np)
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
    try:
        유형 = detect_type(title, spec.get("유형"))
    except TypeConflict as e:
        raise SystemExit(str(e))
    gaejeong = kind == "개정"
    if gaejeong != (유형 != "제정"):
        raise SystemExit("유형 충돌: --type %s 인데 조례 유형은 %s입니다" % (kind, 유형))

    set_text(ps[m["공고번호"]], "경기도의회 공고 %s" % spec.get("공고번호", "제    -    호"))
    warn = []

    def val(key, ph):
        """빈 값이면 눈에 보이는 자리표시자 + 경고(빈칸 두 칸이 그대로 찍히던 결함)."""
        v = str(spec.get(key) or "").strip()
        if not v or "○" in v:
            warn.append(key)
            return v or ph
        return v

    set_text(ps[m["예고문"]], NOTICE.format(위원회=val("소관위원회", "○○○위원회"),
                                            의원=val("대표발의", "○○○"),
                                            조례명=title))
    set_text(ps[m["공고일"]], spec.get("공고일", "    .   .   ."))
    set_text(ps[m["제목행"]], "「%s」" % title)
    set_text(ps[m["공포번호"]], "%s 조례 제    호" % org)
    set_text(ps[m["본문제목"]], "「%s」" % title)
    set_text(ps[m["문의처"]], " ○ 경기도의회 %s" % val("문의처", PH_CONTACT))
    set_text(ps[m["제출기한"]], " 가. 제출기한 : %s" % spec.get("제출기한", "    년   월   일(  )까지"))

    # 서식 정본의 자리표시자가 제출본에 남지 않도록, 값이 없으면
    # 비어 있음이 눈에 보이는 자리표시자로 반드시 덮어쓴다.
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
        set_text(ps[m["개정두문"]], dumun(spec, 유형, warn))
        # 두문은 빌더가 만든다(공유 계약) — 개정문에 두문 줄이 섞여 있으면 걷어낸다
        body = [e for e in (spec.get("개정문") or spec.get("본칙") or [])
                if not RE_DUMUN.match((e.get("text") or "").strip())]
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
        ul_id = None
        if has_ul_marker(pairs):
            hdr, ul_id = add_underline_charpr(items["Contents/header.xml"].decode("utf-8"))
            items["Contents/header.xml"] = hdr.encode("utf-8")
        fill_table(ps[m["대비표"]], pairs, ul_id)
        if spec.get("전부개정") or 유형 == "전부개정":
            set_text(ps[m["대비표항목"]], "4. 주요조문 대비표 : 붙임")
            set_text(ps[m["대비표제목"]], "주요조문 대비표")
        cur = spec.get("현행조례") or []
        if not cur:
            cur = [{"level": "조", "text": "(현행 조례 전문을 여기에 넣습니다 — law-search로 조회)"}]
            warn.append("현행 조례 전문")
        if cur:
            set_text(ps[m["현행조례명"]], "「%s」" % current_name(spec))
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
    # 밑줄 표식(〔U〕·〔/U〕)은 run 분할로 전부 소비돼야 한다 — 글자 그대로 찍히면 결함
    left = UL_ANY.findall(txt)
    if left:
        raise SystemExit("밑줄 표식이 %d개 남았습니다(짝이 안 맞거나 대비표 밖 필드에 있음): %s"
                         % (len(left), txt[max(0, txt.find(left[0]) - 30):txt.find(left[0]) + 40]))
    if gaejeong and has_ul_marker(spec.get("대비표") or []):
        hdr = items["Contents/header.xml"].decode("utf-8")
        ids = re.findall(r'<hh:charPr id="(\d+)"', hdr)
        cnt = int(re.search(r'<hh:charProperties itemCnt="(\d+)"', hdr).group(1))
        last = re.search(r'<hh:charPr id="%s".*?</hh:charPr>' % ids[-1], hdr, flags=re.S).group(0)
        if len(ids) != cnt or 'underline type="BOTTOM"' not in last:
            raise SystemExit("밑줄 charPr이 charProperties 맨 끝에 추가되지 않았습니다")
        if 'charPrIDRef="%s"' % ids[-1] not in items["Contents/section0.xml"].decode("utf-8"):
            raise SystemExit("밑줄 charPr %s 를 참조하는 run이 없습니다" % ids[-1])

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
    try:
        t = detect_type(s.get("조례명", ""), s.get("유형"))
    except TypeConflict as e:
        raise SystemExit(str(e))
    kind = a.kind or ("개정" if t != "제정" else "제정")
    build(s, kind, a.stage)
