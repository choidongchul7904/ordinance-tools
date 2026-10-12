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
    python build_uian.py spec.json stage.hwpx [--template PATH] [--buchik-break off|on]
                         [--compare auto|off] [--keep-newnum]
2026-09-14: 장 제목 가운데정렬(level "장" 또는 "제N장 …"), 공동발의 표기 규격(spec "공동발의"),
            부칙 쪽나눔 기본 off(기준선), 참고사항 점선 정렬은 align_notes.py(후처리).
2026-10-02: 개정조례안이고 spec에 `대비표`가 있으면 부칙 뒤 새 쪽에 「신ㆍ구조문대비표」를
            붙인다(compare_table.py, 의안 제1621호 실물 규격).
2026-10-12: (감사 261012 반영) 유형 판별 통일(detect_type), 개정문 두문 자동 렌더,
            절 레벨, 모르는 레벨 오류, 목 이하 전용 문단모양, RE_HEAD 중첩 괄호,
            가지조 번호 검증, 표 문단 제외, 부칙 제목 4칸(기준선 실측), 표지 [새 번호 지정] 제거.
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
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HC = "http://www.hancom.co.kr/hwpml/2011/core"
NS = {"hp": HP, "hh": HH, "hc": HC}
Q = lambda n: "{%s}%s" % (HP, n)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TPL = os.path.join(HERE, "..", "assets", "uian-template.hwpx")
sys.path.insert(0, HERE)
import compare_table                                   # noqa: E402  (같은 폴더)

# ── 유형 판별(공유 규약 — ordinance-suite·ordinance-format과 같은 규칙, 스킬 독립을 위해 인라인) ──
TYPES = ("제정", "일부개정", "전부개정")


def detect_type(title, explicit=None):
    """제명(공백 전부 제거)에서 유형을 판별한다. 명시 `유형`이 우선하되, 제명이 개정으로
    판별되는데 명시값과 다르면 중단한다."""
    t = re.sub(r"\s+", "", title or "")
    if "전부개정" in t:
        derived = "전부개정"
    elif "일부개정" in t or t.endswith(("개정조례안", "개정규칙안")):
        derived = "일부개정"
    else:
        derived = "제정"
    if not explicit:
        return derived
    ex = re.sub(r"\s+", "", str(explicit))
    if ex == "개정":                                   # 옛 spec 하위호환
        ex = derived if derived != "제정" else "일부개정"
    if ex not in TYPES:
        raise SystemExit("유형 '%s'은(는) 알 수 없습니다 — 사용 가능: %s" % (explicit, ", ".join(TYPES)))
    if derived != "제정" and derived != ex:
        raise SystemExit("유형 충돌: 제명 '%s'은(는) %s인데 spec `유형`은 %s입니다." % (title, derived, ex))
    return ex


def is_amendment(spec):
    return detect_type(spec.get("조례명"), spec.get("유형")) != "제정"


DOUMUN = {"일부개정": "「%s」 일부를 다음과 같이 개정한다.",
          "전부개정": "「%s」 전부를 다음과 같이 개정한다."}
RE_DOUMUN = re.compile(r"다음과\s*같이\s*개정한다")


def current_title(spec):
    """현행조례명(「」 없이). 없으면 제명에서 '…개정조례안'을 걷어 추정하고 경고한다."""
    cur = (spec.get("현행조례명") or "").strip().strip("「」").strip()
    if cur:
        return cur
    t = (spec.get("조례명") or "").strip()
    m = re.match(r"^(.*?)\s*(?:일부\s*개정|전부\s*개정|개정)\s*(조례|규칙)안$", t)
    if not m or not m.group(1).strip():
        raise SystemExit("개정조례안인데 `현행조례명`이 없고 제명에서도 추정할 수 없습니다: %r" % t)
    cur = m.group(1).strip()
    if not cur.endswith(m.group(2)):          # "○○ 지원 일부개정조례안"처럼 '조례'가 빠진 제명
        cur += " " + m.group(2)
    print("경고: `현행조례명`이 없어 제명에서 추정했습니다 → 「%s」 (제출 전 공식 제명과 대조할 것)" % cur)
    return cur


def amendment_entries(spec, kind):
    """개정조례안 본칙 = 두문 + 개정문(없으면 본칙). 개정문 첫 줄이 이미 두문이면 덧붙이지 않는다."""
    body = spec.get("개정문") or spec.get("본칙") or []
    first = (body[0].get("text") or "") if body and isinstance(body[0], dict) else ""
    if RE_DOUMUN.search(first):
        return list(body)
    return [{"level": "지시", "text": DOUMUN[kind] % current_title(spec)}] + list(body)


# ── 계층 ──────────────────────────────────────────────────────────────
# 계층별 앞 공백 — 이 서식은 왼쪽여백이 아니라 '앞 공백 + 내어쓰기'로 계층을 만든다
INDENT = {"조": 0, "지시": 0, "항": 2, "호": 2, "목": 4, "1)": 6, "가)": 8, "(1)": 10, "(가)": 12}
HEADINGS = ("장", "절")                    # 가운데정렬 제목
LEVELS_ALLOWED = HEADINGS + tuple(INDENT)
# 목 이하 내어쓰기(hp:default 분기 값 = 렌더 pt×200, case 분기는 이 값의 1/2 — 모델 paraPr 53의 관례 -2992/-1496).
# 2026-10-12 한/글 실렌더 실측(기준선 실물 완성본): 앞 공백 1칸 = 7pt, 항ㆍ호(공백 2칸)의
# 2992 = 14.96pt = 14 + 0.96 → 둘째 줄이 그 계층 기호 시작보다 약 1pt 오른쪽에서 시작한다.
# 같은 규칙(공백 칸수×7 + 0.96pt)으로 목 이하를 정했다(목 4칸 28.96pt … (가) 12칸 84.96pt).
SUB_INTENT = {"목": 5792, "1)": 8592, "가)": 11392, "(1)": 14192, "(가)": 16992}

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

# 제○조(제목) 머리 — 여기까지만 진하게, 뒤 본문은 보통. 제목 안 괄호 1단 중첩 허용: 제2조(정의(定義))
RE_HEAD = re.compile(r"^(제\d+조(?:의\d+)?\s*\((?:[^()]|\([^()]*\))*\))(.*)$")
# 조문 번호(가지조 포함) — 검증용
RE_JO_NUM = re.compile(r"^제(\d+)조(?:의(\d+))?\s*\(")

BUCHIK_TITLE = "부    칙"     # 기준선 section1[47] 실측: 4칸(2026-10-12 확인)

DEFAULT_NOTES = [
    "가. 관계법령: 덧붙임",
    "나. 입법예고: [○○의회 공고 제    -      호]",
    "   ○ 예고기간:     .    .    .(    ) ~     .    .    .(    )",
    "다. 비용추계서: (○○○과-    ,     .  .  .) ------------- [붙임 1]",
    "라. 조례안 검토의견: (○○○과-    ,     .  .  .) ----------- [붙임 2]",
    "마. 입법예고 결과: (○○○과-    ,     .  .  .) ----- [붙임 3]",
]
# 개정조례안 기본값 — 실물 의안(제1621호)은 참고사항 첫 항목이 신ㆍ구조문 대비표 덧붙임이다.
# (가운뎃점이 ㆍ인지 ·인지는 실물 1621호 원문과 대조할 것 — 확인 전까지 현행 ㆍ 유지)
DEFAULT_NOTES_AMEND = [
    "가. 신ㆍ구조문 대비표: 덧붙임",
    "나. 관계법령: 덧붙임",
    "다. 입법예고: [경기도의회 공고 제    -      호]",
    "   ○ 예고기간:     .    .    .(    ) ~     .    .    .(    )",
    "라. 비용추계서: (예산분석과-    ,     .  .  .) ------------- [붙임 1]",
    "마. 조례안 검토의견: (법제과-    ,     .  .  .) ----------- [붙임 2]",
    "바. 입법예고 결과: (법제과-    ,     .  .  .) ----- [붙임 3]",
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


def set_lines(p, lines):
    """문단 하나에 여러 줄 — 단일 hp:t 안에 hp:lineBreak 로 잇는다(표 셀 rowSpan 이라 표가 자란다)."""
    set_text(p, lines[0])
    t = p.find(".//hp:t", NS)
    for line in lines[1:]:
        lb = etree.SubElement(t, Q("lineBreak"))
        lb.tail = line
    return p


def remove_newnum(root):
    """표지 '발의연월일 :' 칸에 숨은 [새 번호 지정](hp:ctrl/hp:newNum) 조판부호를 지운다.
    완성본을 다른 문서의 붙임으로 복사하면 따라가 쪽번호를 1로 리셋한다(실물 완성본 실측).
    구역설정의 hp:numbering@newNum(속성)은 다른 것이라 건드리지 않는다. 지운 개수 반환."""
    n = 0
    for nn in list(root.iter(Q("newNum"))):
        ctrl = nn.getparent()
        if ctrl is not None and ctrl.tag == Q("ctrl"):
            ctrl.getparent().remove(ctrl)
            n += 1
    return n


def format_proposers(rep, co, per_line=3):
    """발의자 란 표기 규격(실물 의안, 2024.4.5. 경기도의회 상징물 조례안 표지 실측):
    대표발의자를 맨 앞에, 이어 서명부 순서대로 `·`(U+00B7)로 잇고 한 줄에 3명씩 끊은 뒤
    마지막 줄에 `의원(N명)` 을 단독으로 둔다. 공동발의가 없으면 대표 1명만."""
    # 공동발의 항목은 "성명" / "소속|성명" / {"소속":…, "성명":…} 세 꼴을 모두 받는다(suite spec)
    def nm(x):
        if isinstance(x, dict):
            return (x.get("성명") or "").strip()
        s = str(x or "")
        return s.split("|", 1)[1].strip() if "|" in s else s.strip()
    names = [rep] + [nm(n) for n in (co or []) if nm(n) and nm(n) != rep]
    if len(names) == 1:
        return [rep]
    lines = ["·".join(names[i:i + per_line]) for i in range(0, len(names), per_line)]
    lines.append("의원(%d명)" % len(names))
    return lines


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


def clone_plain(model_head, txt):
    """조 문단모양(paraPr 52)이되 진하게 머리 없이 보통 run 하나 — 두문·지시문·머리 없는 조."""
    p = copy.deepcopy(model_head)
    drop_lsa(p)
    runs = text_runs(p)
    keep = runs[-1]                       # 마지막 run = 보통(charPr 32)
    for r in runs[:-1]:
        p.remove(r)
    for t in keep.findall(Q("t")):
        keep.remove(t)
    etree.SubElement(keep, Q("t")).text = txt
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


def add_sub_parapr(hdr, base_id="53"):
    """목 이하 전용 문단모양을 header 문단모양 목록 끝에 추가한다(새 id = 최대 id+1).
    base(53, 항ㆍ호)를 복제해 내어쓰기만 SUB_INTENT 값으로 바꾼다. {level: paraPr id}."""
    lst = hdr.find(".//hh:paraProperties", NS)
    base = lst.find("hh:paraPr[@id='%s']" % base_id, NS)
    if base is None:
        raise SystemExit("서식 header에 paraPr %s(항ㆍ호 모델)가 없습니다" % base_id)
    out = {}
    for lv, val in SUB_INTENT.items():
        new = copy.deepcopy(base)
        nid = str(max(int(e.get("id")) for e in lst.findall("hh:paraPr", NS)) + 1)
        new.set("id", nid)
        for it in new.iter("{%s}intent" % HC):
            in_case = any(etree.QName(a).localname == "case" for a in it.iterancestors())
            it.set("value", str(-(val // 2) if in_case else -val))
        lst.append(new)
        out[lv] = nid
    lst.set("itemCnt", str(len(lst.findall("hh:paraPr", NS))))
    return out


# ---------------------------------------------------------------- 본문 조립
def body_items(entries, where="본문", compact=False):
    """[{level,text}] → 의안 서식 문단 명령.
    ('H',머리,본문) 조문머리 | ('N',텍스트) 조 문단모양 보통 | ('S',텍스트,level) 항 이하 |
    ('C',텍스트) 장ㆍ절 가운데 | ('B',) 빈 줄.
    compact=True(일부개정 개정문): 조 사이에 빈 줄을 자동으로 넣지 않는다.
    미검증 마커가 있는 항목은 guard()가 빈 문자열로 바꾸고 빈 줄(B)로 처리된다."""
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or "text" not in e:
            raise SystemExit("%s %d번째 항목에 `text`가 없습니다: %r" % (where, i + 1, e))
        lv = (e.get("level") or "조").strip()
        if lv not in LEVELS_ALLOWED:
            raise SystemExit("%s %d번째 항목의 레벨 '%s'은(는) 알 수 없습니다 — 사용 가능: %s"
                             % (where, i + 1, lv, "·".join(LEVELS_ALLOWED)))
        txt = guard((e.get("text") or "").strip(), where)
        if not txt:
            out.append(("B",))
            continue
        # 장ㆍ절 제목 — 가운데정렬. level 없이 "제N장 …"으로 들어온 조 줄도 제목으로 보되,
        # "제2장 제목 중 …을 …로 한다."같은 개정 지시문(…다.로 끝남)은 제외한다.
        if lv in HEADINGS or (lv == "조" and re.match(r"^제\d+[장절](\s|$)", txt)
                              and not txt.endswith("다.")):
            if out and out[-1][0] != "B":
                out.append(("B",))
            out.append(("C", txt))
            out.append(("B",))
            continue
        if lv in ("조", "지시"):
            m = RE_HEAD.match(txt)
            if lv == "조" and not compact and m and out and out[-1][0] != "B":
                out.append(("B",))              # 조 사이 빈 줄 1개
            if lv == "조" and m:
                out.append(("H", m.group(1), m.group(2)))
            else:
                out.append(("N", txt))
        else:
            out.append(("S", " " * INDENT[lv] + txt, lv))
    while out and out[0][0] == "B":
        out.pop(0)
    while out and out[-1][0] == "B":        # 끝 빈 줄은 호출자가 넣는다(부칙 앞 빈 줄 중복 방지)
        out.pop()
    return out


def jo_keys(texts):
    """문단 텍스트 목록 → 조문 번호 키 [(n, 가지)] — 가지조(제3조의2) 포함."""
    out = []
    for t in texts:
        m = RE_JO_NUM.match(t)
        if m:
            out.append((int(m.group(1)), int(m.group(2) or 0)))
    return out


def fmt_key(k):
    return "제%d조%s" % (k[0], "의%d" % k[1] if k[1] else "")


def check_order(keys):
    """제정 본칙의 조문 번호 순서 — 엄격 증가, 본조는 1부터 연속(가지조는 직전 본조 뒤)."""
    probs = []
    prev = (0, 0)
    for k in keys:
        if k <= prev:
            probs.append("%s 뒤에 %s" % (fmt_key(prev), fmt_key(k)))
        elif k[1] == 0 and k[0] != prev[0] + 1:
            probs.append("%s 다음이 %s(번호 건너뜀)" % (fmt_key(prev), fmt_key(k)))
        elif k[1] and k[0] != prev[0]:
            probs.append("가지조 %s 앞에 본조 제%d조가 없음" % (fmt_key(k), k[0]))
        prev = max(prev, k)
    return probs


def build(spec, tpl, stage, buchik_break="off", compare="auto", keep_newnum=False):
    del UNVERIFIED[:]
    del compare_table.UNVERIFIED[:]
    title = spec["조례명"]
    kind = detect_type(title, spec.get("유형"))
    amend = kind != "제정"
    pairs = spec.get("대비표") if (compare == "auto" and amend) else None
    if spec.get("대비표") and compare == "auto" and not amend:
        print("경고: `대비표`가 있지만 유형이 제정이라 대비표를 붙이지 않았습니다"
              "(유형을 '일부개정'/'전부개정'으로).")
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

    n_newnum = 0 if keep_newnum else remove_newnum(s0)

    set_text(ps[0], title)                                   # 제명
    set_text(ps[1], "(%s 의원 대표발의)" % spec["대표발의"])
    set_text(ps[9], spec.get("발의연월일", "    년    월    일"))
    co = spec.get("공동발의") or []
    pv = spec.get("발의자")
    if isinstance(pv, list):
        set_lines(ps[12], pv)
    elif co:
        set_lines(ps[12], format_proposers(spec["대표발의"], co))
    else:
        set_text(ps[12], pv or spec["대표발의"])

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
    default_notes = DEFAULT_NOTES_AMEND if amend else DEFAULT_NOTES
    notes = [n.strip() for n in spec.get("참고사항") or default_notes if n.strip()]
    notes = [("   " + n) if n.startswith("○") else n for n in notes]
    ps3 = top_paras(s0)
    nidx = [i for i, p in enumerate(ps3)
            if "".join(p.itertext()).strip().startswith(NOTE_PREFIXES)]
    if nidx:
        replace_block(ps3, nidx[0], nidx[-1], notes, ps3[nidx[0]])

    items["Contents/section0.xml"] = etree.tostring(
        s0, encoding="UTF-8", xml_declaration=True, standalone=True)

    # ═══════ section1 — 조례안 본문 ═══════
    hdr = etree.fromstring(items["Contents/header.xml"])
    s1 = etree.fromstring(items["Contents/section1.xml"])
    if not keep_newnum:
        n_newnum += remove_newnum(s1)
    ps = top_paras(s1)
    # 목 이하 문단모양의 모델 = 서식의 항ㆍ호 모델 문단(ps[6])이 실제로 쓰는 paraPr.
    # id를 53으로 고정하면 header가 다른 서식(공개 배포본 정본 등)에서 빌드가 멈춘다.
    sub_pp = add_sub_parapr(hdr, ps[6].get("paraPrIDRef"))

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
            elif c[0] == "N":
                np = clone_plain(M_HEAD, c[1])
            elif c[0] == "S":
                np = clone(M_SUB, c[1])
                if c[2] in sub_pp:               # 목 이하 — 전용 내어쓰기
                    np.set("paraPrIDRef", sub_pp[c[2]])
            elif c[0] == "C":                    # 장ㆍ절 제목: 같은 문서의 부칙 제목 paraPr/charPr 차용
                np = clone(M_SUPT, c[1])
                np.attrib.pop("pageBreak", None)
            else:
                np = clone(M_BLANK)
            parent.insert(at, np)
            at += 1
        return at

    if amend:
        main = amendment_entries(spec, kind)
    else:
        if spec.get("개정문"):
            print("경고: 유형이 제정인데 `개정문`이 있어 무시했습니다(본칙을 씁니다).")
        main = spec["본칙"]
    at = parent.index(tail)
    at = emit(body_items(main, where="본칙", compact=(kind == "일부개정")), at)

    supp = spec.get("부칙") or [{"level": "조", "text": "이 조례는 공포한 날부터 시행한다."}]
    # 기준선(실물 완성본): 쪽나눔 없이 마지막 조문 뒤 빈 문단 1개를 두고 부칙이 이어진다.
    # --buchik-break on 은 옛 동작(항상 새 쪽) — 부칙만 있는 쪽이 하나 더 생기니 마지막 쪽 여유가 없을 때만.
    parent.insert(at, clone(M_BLANK)); at += 1
    head = clone(M_SUPT, BUCHIK_TITLE)
    head.set("pageBreak", "1" if buchik_break == "on" else "0")
    for np in (head, clone(M_SUPB)):
        parent.insert(at, np)
        at += 1
    emit(body_items(supp, where="부칙"), at)

    # ═══════ (개정) 신ㆍ구조문대비표 — 부칙 뒤 새 쪽, 꼬리 문단(구역 마감) 앞 ═══════
    n_rows = 0
    if pairs:
        kw = {"title": spec["대비표제목"]} if spec.get("대비표제목") else {}
        _, tbl_para = compare_table.insert_into(hdr, s1, pairs, anchor=tail, **kw)
        # 표 뒤에 남는 템플릿 꼬리 빈 문단(2개)은 표가 쪽 끝까지 차면 빈 마지막 쪽을 만든다
        # (2026-10-12 실빌드 확인) — 글자·컨트롤 없는 빈 문단만 걷어낸다.
        for sib in list(tbl_para.itersiblings()):
            if (sib.tag == Q("p") and not "".join(sib.itertext()).strip()
                    and sib.find(".//" + Q("ctrl")) is None and sib.find(".//" + Q("tbl")) is None):
                s1.remove(sib)
    items["Contents/header.xml"] = etree.tostring(
        hdr, encoding="UTF-8", xml_declaration=True, standalone=True)

    items["Contents/section1.xml"] = etree.tostring(
        s1, encoding="UTF-8", xml_declaration=True, standalone=True)
    if pairs:
        n_rows = compare_table.verify_block(items["Contents/section1.xml"], pairs)

    # ═══════ 검증 — 조문 번호(가지조 포함)·순서(표와 표를 품은 문단은 제외) ═══════
    s1v = etree.fromstring(items["Contents/section1.xml"])
    body = ["".join(p.itertext()).strip() for p in s1v.iter(Q("p"))
            if p.find(".//" + Q("tbl")) is None
            and not any(a.tag == Q("tbl") for a in p.iterancestors())]
    got = jo_keys(body)
    want = jo_keys((e.get("text") or "").strip() for e in main
                   if not is_unverified((e.get("text") or "").strip()))
    if got[:len(want)] != want:
        raise SystemExit("조문 번호 불일치\n 생성:%s\n 입력:%s" % (
            [fmt_key(k) for k in got[:len(want)]], [fmt_key(k) for k in want]))
    order_warn = check_order(want) if not amend else []
    whole = "".join(etree.fromstring(items["Contents/section0.xml"]).itertext()) + "".join(s1v.itertext())
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
    print("stage: %s (%d bytes, %s, 조문 %d개%s%s)" % (
        stage, os.path.getsize(stage), kind, len(want),
        ", 신ㆍ구조문대비표 %d행" % n_rows if n_rows else "",
        ", [새 번호 지정] %d개 제거" % n_newnum if n_newnum else ""))
    if order_warn:
        print("경고: 조문 번호 순서 이상(입력 그대로 조판함) — %s" % "; ".join(order_warn))
    if warn:
        print("자리표시자로 채운 항목(제출 전 반드시 채울 것): %s" % ", ".join(warn))
    notes_unv = UNVERIFIED + compare_table.UNVERIFIED
    if notes_unv:
        print("[미검증 — 완성본에서 공란 처리함, 대화창으로만 보고할 것] %d건" % len(notes_unv))
        for note in notes_unv:
            print("  - %s" % note)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("stage")
    ap.add_argument("--template", default=DEFAULT_TPL)
    ap.add_argument("--buchik-break", choices=["off", "on"], default="off",
                    help="부칙 제목 앞 쪽나눔. off=기준선(빈 줄 후 이어짐, 기본) on=항상 새 쪽")
    ap.add_argument("--compare", choices=["auto", "off"], default="auto",
                    help="auto=개정조례안이면 spec `대비표`로 신ㆍ구조문대비표를 붙임(기본) off=붙이지 않음")
    ap.add_argument("--keep-newnum", action="store_true",
                    help="표지 '발의연월일' 칸의 [새 번호 지정] 조판부호를 지우지 않고 둔다(기본: 제거)")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        build(json.load(f), a.template, a.stage, a.buchik_break, a.compare, a.keep_newnum)
