# -*- coding: utf-8 -*-
"""신ㆍ구조문대비표 블록을 HWPX 구역 끝에 덧붙인다 — 완성본(의안)·표준서식 공용.

서식 정본은 assets/compare-table-template.hwpx (경기도의회 의안 제1621호
「경기도 청소년 보호 및 육성에 관한 조례 일부개정조례안」(2025.1.)의 대비표를
잘라낸 것). 경기도의회 실물 규격:

  · 제목 「신ㆍ구조문대비표」(새 쪽, 가운데, 16pt 진하게)
  · 2열 표 — 머리행 「현        행」/「개   정   안」(사방 테두리)
  · 본문은 **줄 단위 행**(가로선 없음, 좌우선만) — 조 줄은 내어쓰기 14pt·180%,
    항ㆍ호 줄은 앞 공백 2칸·내어쓰기 24.4pt. 좌우 열의 줄이 1:1로 맞물린다.
  · 머리행 바로 밑 간격행(283)과 마감행(아래선)으로 닫는다.

대비표 데이터는 ordinance-suite spec의 `대비표`: [["현행 셀", "개정안 셀"], …]
(셀 안 줄바꿈 `\\n`, 바뀐 자구는 `〔U〕…〔/U〕`로 감싸면 밑줄).

2026-10-12(감사 261012): 셀 단위 미검증 마커 가드(마커 셀 → 공란, UNVERIFIED로 보고, 중단 안 함),
목·1)·가)·(1)·(가) 줄의 계층 공백·내어쓰기 보존, 머리행 반복(repeatHeader + 머리행 셀 header="1"),
표 id 결정화(crc32), 제목 `title` 인자/`--title`(기본 ㆍ = 1621호 자산 표기 — 다른 실물은 대조할 것).

다른 문서에 옮겨 심을 때 글자모양ㆍ문단모양ㆍ테두리ㆍ글꼴을 **대상 문서 header의
목록 끝에 복사**하고 id를 다시 매긴다(중간에 끼우면 한/글 재저장 때 id가 밀린다).

usage (단독):
    python compare_table.py in.hwpx out.hwpx pairs.json [--section 0] [--font 신명조]
    (out.hwpx 는 스테이징 — com_save.py 로 정품 재저장할 것)
"""
import argparse
import copy
import json
import os
import re
import sys
import zipfile
import zlib

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
HH = "http://www.hancom.co.kr/hwpml/2011/head"
HS = "http://www.hancom.co.kr/hwpml/2011/section"
Q = lambda n: "{%s}%s" % (HP, n)
H = lambda n: "{%s}%s" % (HH, n)

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, "..", "assets", "compare-table-template.hwpx")

TITLE = "신ㆍ구조문대비표"
UL_OPEN, UL_CLOSE = "〔U〕", "〔/U〕"
UL_SPLIT = re.compile("(%s.*?%s)" % (re.escape(UL_OPEN), re.escape(UL_CLOSE)))
RE_JO_HEAD = re.compile(r"^\s*제\d+조(?:의\d+)?")
# 줄 머리 기호 → (계층, 앞 공백 칸수). 표준 입안 서식과 같은 계층 공백(호 2·목 4·1) 6·가) 8·(1) 10·(가) 12).
# 조 줄과 <신 설>/<삭 제> 표식 줄은 RE_JO_HEAD·기본값(2칸)으로 처리한다.
LINE_LEVELS = [
    (re.compile(r"^[①-⑳㉑-㉟㊱-㊿]"), "항", 2),   # ①~⑳ ㉑~㊿
    (re.compile(r"^\d+\.(?!\d)"), "호", 2),
    (re.compile(r"^[가-힣]\.(?!\S)"), "목", 4),
    (re.compile(r"^\d+\)"), "1)", 6),
    (re.compile(r"^[가-힣]\)"), "가)", 8),
    (re.compile(r"^\(\d+\)"), "(1)", 10),
    (re.compile(r"^\([가-힣]\)"), "(가)", 12),
]
# 목 이하 줄의 내어쓰기(자산 m_ho 문단모양 관례: hp:case 값=아래 값, hp:default=2배).
# 자산(1621호)의 조 1400·호 2440과 같은 표준 입안 서식 계열 값 — 1621호에 목 이하 실례가 없어 표준값 차용.
SUB_INTENT = {"목": 4540, "1)": 5420, "가)": 7520, "(1)": 8780, "(가)": 10880}

# ── 미검증 콘텐츠 가드(셀 단위) ─────────────────────────────────────────
# 셀 텍스트에 "[원문 미확인 …]"/"[확인 필요 …]" 류 마커가 있으면 빌드를 멈추지 않고 그 셀을
# 공란으로 만든다. 원문은 UNVERIFIED에 적재해 호출자가 대화창으로만 보고한다.
UNVERIFIED_RE = re.compile(r"\[\s*(?:원문\s*미확인|미확인|확인\s*필요|검증\s*필요|미검증)[^\]]*\]")
UNVERIFIED = []


def clean_pairs(pairs, report=True):
    """마커가 든 셀 → 빈 셀. report=True면 UNVERIFIED에 (행, 열, 원문)을 적재한다."""
    out = []
    for i, pr in enumerate(pairs or []):
        if not isinstance(pr, (list, tuple)) or len(pr) != 2:
            raise SystemExit("대비표 %d번째 항목이 [현행, 개정안] 쌍이 아닙니다: %r" % (i + 1, pr))
        row = []
        for side, cell in zip(("현행", "개정안"), pr):
            cell = "" if cell is None else str(cell)
            if UNVERIFIED_RE.search(cell):
                if report:
                    UNVERIFIED.append("대비표 %d행 %s: %s" % (i + 1, side, cell.strip()))
                cell = ""
            row.append(cell)
        out.append(row)
    return out


LANGS =("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")


def _drop_lsa(el):
    for p in el.iter(Q("p")):
        for lsa in p.findall(Q("linesegarray")):
            p.remove(lsa)


# ---------------------------------------------------------------- 자산 로드
def load_asset(path=ASSET):
    """(header root, title 문단, 표 문단) — 표 문단은 머리행·간격행·조 모델·호 모델·마감행 5행."""
    if not os.path.exists(path):
        raise SystemExit("대비표 서식 자산이 없습니다: %s\n"
                         "(assets/compare-table-template.hwpx — 실물 의안 대비표를 잘라낸 규격 자산. "
                         "대비표 없이 만들려면 --compare off)" % os.path.normpath(path))
    z = zipfile.ZipFile(path)
    hdr = etree.fromstring(z.read("Contents/header.xml"))
    sec = etree.fromstring(z.read("Contents/section0.xml"))
    z.close()
    ps = [c for c in sec if c.tag == Q("p")]
    title = next(p for p in ps if "".join(p.itertext()).strip() == TITLE)
    tblp = next(p for p in ps if p.find(".//" + Q("tbl")) is not None)
    tbl = tblp.find(".//" + Q("tbl"))
    if len(tbl.findall(Q("tr"))) != 5:
        raise SystemExit("대비표 자산의 행 수가 5가 아닙니다: %s" % path)
    return hdr, title, tblp


# ---------------------------------------------------------------- header 이식
class Transplant:
    """자산 header의 정의(글꼴·테두리·글자모양·탭·문단모양)를 대상 header 끝에 복사하고
    id 대응표를 만든다. font_override 를 주면 모든 글꼴을 대상 문서의 그 글꼴로 맞춘다."""

    def __init__(self, src_hdr, dst_hdr, font_override=None):
        self.src, self.dst = src_hdr, dst_hdr
        self.font_override = font_override
        self.map = {"charPr": {}, "paraPr": {}, "borderFill": {}, "tabPr": {}}
        self.fontmap = {}          # (lang, src_id) -> dst_id

    # -- 목록 접근
    def _list(self, root, tag):
        el = root.find(".//" + H(tag))
        if el is None:
            raise SystemExit("header에 %s 가 없습니다" % tag)
        return el

    @staticmethod
    def _next_id(lst, item_tag):
        # borderFill 은 id가 1부터, 나머지는 0부터 시작한다 — itemCnt 를 새 id로 쓰면
        # 기존 마지막 id와 충돌해 엉뚱한 테두리가 그려진다. 항상 최대 id + 1.
        ids = [int(e.get("id")) for e in lst.findall(item_tag) if e.get("id", "").isdigit()]
        return str(max(ids) + 1) if ids else "0"

    def _append(self, root, list_tag, item_tag, elem):
        lst = self._list(root, list_tag)
        new_id = self._next_id(lst, H(item_tag))
        elem.set("id", new_id)
        lst.append(elem)
        lst.set("itemCnt", str(len(lst.findall(H(item_tag)))))
        return new_id

    def _find(self, root, list_tag, item_tag, id_):
        lst = self._list(root, list_tag)
        el = lst.find("%s[@id='%s']" % (H(item_tag), id_))
        if el is None:
            raise SystemExit("header %s 에 id=%s 가 없습니다" % (list_tag, id_))
        return el

    # -- 글꼴
    def font(self, lang, src_id):
        key = (lang, src_id)
        if key in self.fontmap:
            return self.fontmap[key]
        L = lang.upper()
        src_ff = self.src.find(".//%s[@lang='%s']" % (H("fontface"), L))
        dst_ff = self.dst.find(".//%s[@lang='%s']" % (H("fontface"), L))
        face = self.font_override
        src_font = None
        if face is None:
            src_font = src_ff.find("%s[@id='%s']" % (H("font"), src_id))
            face = src_font.get("face")
        hit = dst_ff.find("%s[@face='%s']" % (H("font"), face))
        if hit is not None:
            dst_id = hit.get("id")
        else:
            if src_font is None:   # override 글꼴이 대상에 없으면 자산 글꼴을 그대로 심는다
                src_font = src_ff.find("%s[@id='%s']" % (H("font"), src_id))
            new = copy.deepcopy(src_font)
            dst_id = self._next_id(dst_ff, H("font"))
            new.set("id", dst_id)
            dst_ff.append(new)
            dst_ff.set("fontCnt", str(len(dst_ff.findall(H("font")))))
        self.fontmap[key] = dst_id
        return dst_id

    # -- 테두리/배경
    def border_fill(self, src_id):
        m = self.map["borderFill"]
        if src_id in m:
            return m[src_id]
        new = copy.deepcopy(self._find(self.src, "borderFills", "borderFill", src_id))
        m[src_id] = self._append(self.dst, "borderFills", "borderFill", new)
        return m[src_id]

    def tab_pr(self, src_id):
        m = self.map["tabPr"]
        if src_id in m:
            return m[src_id]
        new = copy.deepcopy(self._find(self.src, "tabProperties", "tabPr", src_id))
        m[src_id] = self._append(self.dst, "tabProperties", "tabPr", new)
        return m[src_id]

    def char_pr(self, src_id, underline=False):
        key = src_id + ("+U" if underline else "")
        m = self.map["charPr"]
        if key in m:
            return m[key]
        new = copy.deepcopy(self._find(self.src, "charProperties", "charPr", src_id))
        fr = new.find(H("fontRef"))
        for lang in LANGS:
            if fr.get(lang) is not None:
                fr.set(lang, self.font(lang, fr.get(lang)))
        if new.get("borderFillIDRef"):
            new.set("borderFillIDRef", self.border_fill(new.get("borderFillIDRef")))
        ul = new.find(H("underline"))
        if underline:
            if ul is None:
                raise SystemExit("charPr %s 에 underline 요소가 없습니다" % src_id)
            ul.set("type", "BOTTOM")
        m[key] = self._append(self.dst, "charProperties", "charPr", new)
        return m[key]

    def para_pr(self, src_id):
        m = self.map["paraPr"]
        if src_id in m:
            return m[src_id]
        new = copy.deepcopy(self._find(self.src, "paraProperties", "paraPr", src_id))
        if new.get("tabPrIDRef"):
            new.set("tabPrIDRef", self.tab_pr(new.get("tabPrIDRef")))
        # 문단 테두리(hh:border borderFillIDRef)도 참조다 — 빠뜨리면 대상 문서의 엉뚱한
        # 테두리(예: 셀 좌우선)가 문단마다 그려져 세로선이 끊긴 것처럼 보인다(실측).
        for b in new.iter(H("border")):
            if b.get("borderFillIDRef"):
                b.set("borderFillIDRef", self.border_fill(b.get("borderFillIDRef")))
        m[src_id] = self._append(self.dst, "paraProperties", "paraPr", new)
        return m[src_id]

    # -- 조각 전체의 참조를 대상 id로 바꾼다
    def para_variant(self, dst_id, intent):
        """대상 header의 문단모양 dst_id를 복제해 내어쓰기만 바꾼 변형(목 이하 줄용).
        관례는 자산 m_ho와 같다: hp:case 분기 = -intent, hp:default 분기 = -2*intent."""
        key = (dst_id, intent)
        m = self.map.setdefault("paraVar", {})
        if key in m:
            return m[key]
        new = copy.deepcopy(self._find(self.dst, "paraProperties", "paraPr", dst_id))
        for it in new.iter("{http://www.hancom.co.kr/hwpml/2011/core}intent"):
            in_case = any(etree.QName(a).localname == "case" for a in it.iterancestors())
            it.set("value", str(-intent if in_case else -2 * intent))
        m[key] = self._append(self.dst, "paraProperties", "paraPr", new)
        return m[key]

    def remap(self, elem):
        for e in elem.iter():
            if e.get("charPrIDRef") is not None:
                e.set("charPrIDRef", self.char_pr(e.get("charPrIDRef")))
            if e.get("paraPrIDRef") is not None:
                e.set("paraPrIDRef", self.para_pr(e.get("paraPrIDRef")))
            if e.get("borderFillIDRef") is not None:
                e.set("borderFillIDRef", self.border_fill(e.get("borderFillIDRef")))
            if e.get("styleIDRef") is not None:
                e.set("styleIDRef", "0")
        return elem


# ---------------------------------------------------------------- 표 조립
def _set_runs(p, txt, base, ul):
    """셀 문단 하나를 txt로. 〔U〕…〔/U〕 안쪽은 밑줄 charPr(ul) run, 바깥은 base run.
    base·ul 은 **대상 문서 id** — 이 문단은 이미 remap 된 뒤라 다시 remap 하면 안 된다."""
    for r in p.findall(Q("run")):
        p.remove(r)
    segs = []
    for seg in UL_SPLIT.split(txt):
        if not seg:
            continue
        if seg.startswith(UL_OPEN) and seg.endswith(UL_CLOSE):
            inner = seg[len(UL_OPEN):-len(UL_CLOSE)]
            if inner:
                segs.append((inner, ul))
        else:
            segs.append((seg, base))
    if not segs:
        segs = [("", base)]
    for text, cid in segs:
        r = etree.SubElement(p, Q("run"))
        r.set("charPrIDRef", cid)
        etree.SubElement(r, Q("t")).text = text
    _drop_lsa(p)


def _plain(ln):
    return ln.replace(UL_OPEN, "").replace(UL_CLOSE, "")


def line_level(ln):
    """줄 → 계층 이름('조'·'항'·'호'·'목'·'1)'·'가)'·'(1)'·'(가)'·'기타'·'빈'). 밑줄 표식은 무시."""
    s = _plain(ln).strip()
    if not s:
        return "빈"
    if RE_JO_HEAD.match(s):
        return "조"
    for rx, lv, _ in LINE_LEVELS:
        if rx.match(s):
            return lv
    return "기타"


def _lines(cell):
    """셀 → 줄 목록. 앞 공백이 없는 줄은 머리 기호로 계층을 판별해 앞 공백을 붙인다
    (조 0칸, 항ㆍ호ㆍ<신 설>ㆍ기타 2칸, 목 4칸, 1) 6칸, 가) 8칸, (1) 10칸, (가) 12칸).
    이미 앞 공백이 있는 줄은 입력 그대로 둔다."""
    spaces = {lv: n for _, lv, n in LINE_LEVELS}
    out = []
    for ln in str(cell or "").split("\n"):
        ln = ln.rstrip()
        if ln and not ln.startswith(" ") and line_level(ln) != "조":
            ln = " " * spaces.get(line_level(ln), 2) + ln
        out.append(ln)
    return out


def build_block(pairs, src_hdr, title_p, tbl_p, tp, title=TITLE, group_gap=True):
    """(제목 문단, 표 문단) — 대상 header에 이식된 id로 완성된 요소."""
    title_p = copy.deepcopy(title_p)
    title_p.set("pageBreak", "1")
    _drop_lsa(title_p)
    t = title_p.find(".//" + Q("t"))
    t.text = title
    tp.remap(title_p)

    tbl_p = copy.deepcopy(tbl_p)
    tbl = tbl_p.find(".//" + Q("tbl"))
    head, spacer, m_jo, m_ho, closing = tbl.findall(Q("tr"))
    for tr in (head, spacer, m_jo, m_ho, closing):
        tbl.remove(tr)
    tp.remap(tbl_p)                      # 표 문단·표 자체의 참조는 여기서 한 번만

    # 모델 행의 셀 기본 글자모양(자산 id) → 대상 id / 밑줄 변형 id. remap 전에 읽는다.
    def cell_ids(model):
        p = model.find(".//" + Q("p"))
        src = p.find(Q("run")).get("charPrIDRef")
        return tp.char_pr(src), tp.char_pr(src, underline=True)
    ids = {id(m_jo): cell_ids(m_jo), id(m_ho): cell_ids(m_ho)}

    def row(model, left, right):
        base, ul = ids[id(model)]
        tr = tp.remap(copy.deepcopy(model))       # 행마다 remap 은 한 번
        for tc, txt in zip(tr.findall(Q("tc")), (left, right)):
            sub = tc.find(Q("subList"))
            ps = sub.findall(Q("p"))
            for extra in ps[1:]:
                sub.remove(extra)
            _set_runs(ps[0], txt, base, ul)
            lv = line_level(txt)
            if model is m_ho and lv in SUB_INTENT:    # 목 이하 — 계층별 내어쓰기 변형
                ps[0].set("paraPrIDRef", tp.para_variant(ps[0].get("paraPrIDRef"), SUB_INTENT[lv]))
        return tr

    # 머리행은 쪽이 넘어가도 반복되게 한다(tbl repeatHeader="1" + 머리행 셀 header="1")
    head_row = tp.remap(copy.deepcopy(head))
    for tc in head_row.findall(Q("tc")):
        tc.set("header", "1")
    tbl.set("repeatHeader", "1")
    rows = [head_row, tp.remap(copy.deepcopy(spacer))]
    pairs = clean_pairs(pairs)                    # 마커 셀 → 공란(중단하지 않음)
    for gi, (left, right) in enumerate(pairs):
        L, R = _lines(left), _lines(right)
        n = max(len(L), len(R))
        L += [""] * (n - len(L))
        R += [""] * (n - len(R))
        if gi and group_gap:
            rows.append(row(m_ho, "", ""))
        for l, r in zip(L, R):
            model = m_jo if "조" in (line_level(l), line_level(r)) else m_ho
            rows.append(row(model, l, r))
    rows.append(tp.remap(copy.deepcopy(closing)))

    for ri, tr in enumerate(rows):
        for tc in tr.findall(Q("tc")):
            tc.find(Q("cellAddr")).set("rowAddr", str(ri))
        tbl.append(tr)
    tbl.set("rowCnt", str(len(rows)))
    # 결정적 id — hash()는 실행마다 값이 바뀌므로(PYTHONHASHSEED) 내용의 crc32를 쓴다
    blob = json.dumps([pairs, title], ensure_ascii=False).encode("utf-8")
    tbl.set("id", str(1700000000 + zlib.crc32(blob) % 90000000))
    _drop_lsa(tbl_p)
    return title_p, tbl_p


def insert_into(dst_hdr, sec, pairs, anchor=None, font_override=None, title=TITLE):
    """이미 파싱된 header root·구역 root에 블록을 넣는다(빌더 내부용).
    anchor: 구역의 최상위 hp:p 요소 — 그 앞에 넣는다. None이면 구역 끝."""
    src_hdr, title_p, tbl_p = load_asset()
    tp = Transplant(src_hdr, dst_hdr, font_override)
    t_p, b_p = build_block(pairs, src_hdr, title_p, tbl_p, tp, title=title)
    if anchor is None:
        sec.append(t_p)
        sec.append(b_p)
    else:
        at = sec.index(anchor)
        sec.insert(at, t_p)
        sec.insert(at + 1, b_p)
    return t_p, b_p


def append_block(items, section_name, pairs, before=None, font_override=None, title=TITLE):
    """items(dict: zip 경로→bytes)의 header.xml·section을 고쳐 대비표 블록을 넣는다.
    before: 구역 안 문단 인덱스(최상위 hp:p 순번) — 그 문단 앞에 넣는다. None이면 구역 끝."""
    if not pairs:
        return items
    dst_hdr = etree.fromstring(items["Contents/header.xml"])
    sec = etree.fromstring(items[section_name])
    paras = [c for c in sec if c.tag == Q("p")]
    anchor = None if before is None else paras[before]
    insert_into(dst_hdr, sec, pairs, anchor, font_override, title)
    items["Contents/header.xml"] = etree.tostring(dst_hdr, encoding="UTF-8",
                                                  xml_declaration=True, standalone=True)
    items[section_name] = etree.tostring(sec, encoding="UTF-8",
                                         xml_declaration=True, standalone=True)
    return items


def append_to_file(src_hwpx, dst_hwpx, pairs, section=0, font_override=None, title=TITLE):
    z = zipfile.ZipFile(src_hwpx)
    order = z.namelist()
    items = {n: z.read(n) for n in order}
    z.close()
    append_block(items, "Contents/section%d.xml" % section, pairs,
                 font_override=font_override, title=title)
    if os.path.exists(dst_hwpx):
        os.remove(dst_hwpx)
    zo = zipfile.ZipFile(dst_hwpx, "w", zipfile.ZIP_DEFLATED)
    zi = zipfile.ZipInfo("mimetype")
    zi.compress_type = zipfile.ZIP_STORED
    zo.writestr(zi, items["mimetype"])
    for n in order:
        if n != "mimetype":
            zo.writestr(n, items[n])
    zo.close()
    return dst_hwpx


def verify_block(section_xml_bytes, pairs):
    """표 안에 표식이 남지 않았고, 행 수가 기대와 같은지."""
    sec = etree.fromstring(section_xml_bytes)
    tbls = [t for t in sec.iter(Q("tbl")) if "".join(t.itertext()).find("현        행") >= 0]
    if not tbls:
        raise SystemExit("대비표 표를 찾지 못했습니다")
    tbl = tbls[-1]
    text = "".join(tbl.itertext())
    if UL_OPEN in text or UL_CLOSE in text:
        raise SystemExit("대비표에 밑줄 표식이 남았습니다")
    if UNVERIFIED_RE.search(text):
        raise SystemExit("대비표에 미검증 마커가 남았습니다 — clean_pairs() 경로를 확인할 것")
    pairs = clean_pairs(pairs, report=False)     # build_block과 같은 셀 가드(보고는 한 번만)
    want = 2 + sum(max(len(_lines(l)), len(_lines(r))) for l, r in pairs) + max(len(pairs) - 1, 0) + 1
    got = len(tbl.findall(Q("tr")))
    if got != want:
        raise SystemExit("대비표 행 수 불일치: 생성 %d, 기대 %d" % (got, want))
    return got


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("pairs", help="JSON: [[현행, 개정안], …] 또는 suite.json(대비표 키)")
    ap.add_argument("--section", type=int, default=0)
    ap.add_argument("--font", default=None, help="모든 글꼴을 이 글꼴로(예: 신명조)")
    ap.add_argument("--title", default=TITLE,
                    help="대비표 제목(기본 '신ㆍ구조문대비표' — 자산=의안 제1621호 실물 표기. 다른 실물과 대조 후 바꿀 것)")
    a = ap.parse_args()
    data = json.load(open(a.pairs, encoding="utf-8"))
    pairs = data["대비표"] if isinstance(data, dict) else data
    append_to_file(a.src, a.dst, pairs, a.section, a.font, title=a.title)
    z = zipfile.ZipFile(a.dst)
    n = verify_block(z.read("Contents/section%d.xml" % a.section), pairs)
    print("대비표 %d행 삽입: %s" % (n, a.dst))
    if UNVERIFIED:
        print("[미검증 — 대비표에서 공란 처리함, 대화창으로만 보고할 것] %d건" % len(UNVERIFIED))
        for note in UNVERIFIED:
            print("  - %s" % note)
