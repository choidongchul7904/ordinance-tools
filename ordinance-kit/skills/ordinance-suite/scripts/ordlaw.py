# -*- coding: utf-8 -*-
"""조례 조문 AST — 파서 · 개정 지시문 컴파일러 · 적용기 · 신구조문 대비표 렌더러.

개정 지시문은 텍스트 치환이 아니라 **조문 트리에 대한 편집 연산**이다.
그래서 이렇게 처리한다:

    현행 원문 → parse()      → 현행 AST (노드마다 불변 ID)
    개정문     → compile()    → 편집 연산 [Op]
    AST + Ops → apply()      → 개정 후 AST
    두 AST     → table()      → 대비표 (신설·삭제·이동이 diff의 결과로 나온다)

설계 근거는 `개정조례_대비표_알고리즘_기획안_260818.md`.
표기 관행(줄표 개수·`(생  략)` 등)은 경기도의회 일부개정조례안 4건을 실측해 정했다.
"""
import re
import unicodedata
from difflib import SequenceMatcher

# ── 마커 ────────────────────────────────────────────────────────────────────
RE_JO = re.compile(r"^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)]*)\))?")
RE_HANG = re.compile(r"^\s*([①-⑳])\s*")
RE_HO = re.compile(r"^\s*(\d{1,2})\.\s")
RE_MOK = re.compile(r"^\s*([가-힣])\.\s")
RE_HEADING = re.compile(r"^\s*제\s*\d+\s*[편장절관](?:\s|$)")
# 개정이력 표기 — 조문 '내용'이 아니라 메타데이터다. 실측 대비표의 현행 열에는
# `<개정 2025.5.7.>` `[단서개정 …]` 이 없다. 파싱 단계에서 걷어낸다.
RE_HIST = re.compile(r"[<\[]\s*(?:본조|단서|제목|전문)?"
                     r"(?:개정|신설|삭제|이동|종전|시행일)?[\s\d.,·~\-년월일]*[>\]]")

HANG_NUM = {c: i + 1 for i, c in enumerate("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")}
NUM_HANG = {v: k for k, v in HANG_NUM.items()}

LEVELS = ("조", "항", "호", "목")


class Node:
    _seq = 0

    def __init__(self, level, num=None, branch=None, title=None, text="", parent=None):
        Node._seq += 1
        self.id = "n%d" % Node._seq       # 불변 — 재번호돼도 이 값으로 좌우를 짝짓는다
        self.level = level
        self.num = num                    # 조 5, 항 2, 호 7 …
        self.branch = branch              # 제5조의2 의 2
        self.title = title                # 조 제목
        self.text = text                  # 마커를 뺀 본문
        self.children = []
        self.parent = parent
        self.origin = None                # 재번호 전 번호 (대비표 '종전의 제N조' 용)

    # ── 표기 ────────────────────────────────────────────────────────────
    @property
    def marker(self):
        if self.level == "조":
            s = "제%d조" % self.num
            if self.branch:
                s += "의%d" % self.branch
            return s + ("(%s)" % self.title if self.title else "")
        if self.level == "항":
            return NUM_HANG.get(self.num, "①")
        if self.level == "호":
            return "%d." % self.num
        if self.level == "목":
            return "%s." % self.num
        return ""

    def line(self):
        m = self.marker
        if self.level == "조":
            return (m + " " + self.text).rstrip() if self.text else m
        return (m + " " + self.text).rstrip()

    def render(self):
        """표기용 텍스트. 조의 제1항은 조 머리 줄에 다시 붙인다(표준 표기)."""
        kids = list(self.children)
        head = self.line()
        if self.level == "조" and not self.text and kids and kids[0].level == "항" \
                and kids[0].num == 1:
            first = kids.pop(0)
            head = (head + " " + first.line()).strip()
            out = [head] + [c.render() for c in first.children]
        else:
            out = [head]
        out += [c.render() for c in kids]
        return "\n".join(out)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, level, num, branch=None):
        for c in self.children:
            if c.level == level and c.num == num and (branch is None or c.branch == branch):
                return c
        return None

    def __repr__(self):
        return "<%s %s %s>" % (self.level, self.marker, self.text[:20])


# ── 1단계: 파서 ─────────────────────────────────────────────────────────────
def parse(entries):
    """조문 텍스트 → [조 Node].

    입력 형식이 두 가지라 **줄 단위 스트림**으로 처리한다.
      · API 산출: 한 조가 줄바꿈으로 항·호를 품은 하나의 항목
      · HWPX 문단 추출: 항·호가 각각 별도 항목
    둘 다 평탄화하면 같은 줄 스트림이 되고, 조 마커가 나올 때마다 새 조를 연다.
    """
    lines = []
    for e in entries:
        t = e["text"] if isinstance(e, dict) else e
        lines.extend(ln for ln in (t or "").split("\n") if ln.strip())

    out, jo, stack, cur = [], None, {}, None
    for ln in lines:
        if RE_HEADING.match(ln):          # 장·절 제목은 조문이 아니다
            continue
        mj = RE_JO.match(ln)
        if mj:
            rest = ln[mj.end():].strip()
            jo = Node("조", int(mj.group(1)), int(mj.group(2)) if mj.group(2) else None,
                      mj.group(3), rest)
            out.append(jo)
            stack = {}
            cur = jo
            # 제1항은 조 머리 줄에 이어 붙는 것이 표준 표기다
            # (`제2조(설치 및 구성) ① 경기도의회에 …`). 구조상으로는 항이므로 분리해야
            # `제2조제1항` 을 지정할 수 있다. 표기는 render() 가 다시 붙인다.
            mh = RE_HANG.match(rest)
            if mh:
                jo.text = ""
                n = Node("항", HANG_NUM[mh.group(1)], text=rest[mh.end():].strip(), parent=jo)
                jo.children.append(n)
                stack = {"항": n}
                cur = n
            continue
        if jo is None:                    # 조보다 앞선 줄은 버린다
            continue
        mh, mo, mk = RE_HANG.match(ln), RE_HO.match(ln), RE_MOK.match(ln)
        if mh:
            n = Node("항", HANG_NUM[mh.group(1)], text=ln[mh.end():].strip(), parent=jo)
            jo.children.append(n)
            stack = {"항": n}
            cur = n
        elif mo:
            p = stack.get("항", jo)
            n = Node("호", int(mo.group(1)), text=ln[mo.end():].strip(), parent=p)
            p.children.append(n)
            stack["호"] = n
            cur = n
        elif mk:
            p = stack.get("호") or stack.get("항") or jo
            n = Node("목", mk.group(1), text=ln[mk.end():].strip(), parent=p)
            p.children.append(n)
            cur = n
        else:                             # 마커 없는 줄 = 앞 노드의 연속행
            cur.text = (cur.text + " " + ln.strip()).strip()
    return out


def index(articles):
    """조번호 → Node. '5', '5의2' 키."""
    ix = {}
    for a in articles:
        ix["%d의%d" % (a.num, a.branch) if a.branch else str(a.num)] = a
    return ix


# ── 문장 성분 (본문 / 단서 / 각 호 외의 부분) ────────────────────────────────
RE_DANSEO = re.compile(r"다만,")


def scope_span(node, scope):
    """치환 범위를 텍스트 오프셋으로 좁힌다. (start, end)"""
    t = node.text
    if scope == "단서":
        m = RE_DANSEO.search(t)
        return (m.start(), len(t)) if m else (0, len(t))
    if scope == "본문":
        m = RE_DANSEO.search(t)
        return (0, m.start()) if m else (0, len(t))
    if scope in ("전단", "후단"):
        # 전단·후단은 한 항 안의 두 문장이다. 첫 마침표를 경계로 본다.
        m = re.search(r"\.\s", t)
        if m:
            return (0, m.end()) if scope == "전단" else (m.end(), len(t))
    return (0, len(t))          # 각 호 외의 부분 = 항 본문 자체(호는 자식이라 분리돼 있다)


# ── 2단계: 지시문 컴파일러 ──────────────────────────────────────────────────
# 실물 지시문(경기도의회 일부개정조례안 4건)에서 확인한 문형:
#   · 절 연결어가 "하고," 뿐 아니라 **"하며,"** 도 쓰인다
#   · "제1항 및 제2항 중 … 각각 …" — **복수 대상 나열**
#   · "제4조 제목 외의 부분을 제1항으로 한다" — **PROMOTE**(제목외 → 항 승격)
#   · "제6조 중 …" — 항이 없는 조를 **직접 지정**
#   · "같은 조/같은 항" **대명사 참조**
Q1, Q2 = "\u201c", "\u201d"            # “ ”
# “A”를 “B”로 / “A”을 **각각** “B”로 — 복수 대상일 때 '각각'이 끼어든다
RE_PAIR = re.compile(r"[%s\"']([^%s\"']*)[%s\"']\s*(?:을|를)\s*(?:각각\s*)?"
                     r"[%s\"']([^%s\"']*)[%s\"']" % (Q1, Q2, Q2, Q1, Q2, Q2))
RE_TITLE_OF = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*의?\s*제목")
RE_JO_REF = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
RE_HANG_REF = re.compile(r"제\s*(\d+)\s*항")
RE_HO_REF = re.compile(r"제\s*(\d+)\s*호")
SPLIT_CLAUSE = re.compile(r"(?:하고|하며|하여),\s*")
# 신설 본문 판별: `제18조의2(회의출석…) ① …` 처럼 **조 제목 괄호**가 있어야 본문이다
RE_BODY_JO = re.compile(r"^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?\s*\(")
# 자치법규입안길라잡이(2022) 제3편 제2장 실측 문형:
#   · 제4조부터 제6조까지를 각각 제5조부터 제7조까지로 하고    → 범위 재번호
#   · 제3조를 제3조의2로 하고                                 → 단일 재번호(가지번호 포함)
#   · 제6조(종전의 제5조) 중 …                                → 재번호 **후** 번호로 지칭
#   · 제○조부터 제○조까지를 각각 삭제한다                      → 범위 삭제
RE_RANGE_RENUM = re.compile(
    r"제\s*(\d+)\s*조(?:의(\d+))?부터\s*제\s*(\d+)\s*조(?:의(\d+))?까지를\s*각각\s*"
    r"제\s*(\d+)\s*조(?:의(\d+))?부터\s*제\s*(\d+)\s*조(?:의(\d+))?까지로")
RE_ONE_RENUM = re.compile(
    r"제\s*(\d+)\s*조(?:의(\d+))?를\s*제\s*(\d+)\s*조(?:의(\d+))?로")
RE_HO_RANGE_RENUM = re.compile(
    r"제\s*(\d+)\s*호부터\s*제\s*(\d+)\s*호까지를\s*각각\s*제\s*(\d+)\s*호")
RE_RANGE_DEL = re.compile(
    r"제\s*(\d+)\s*조(?:의(\d+))?부터\s*제\s*(\d+)\s*조(?:의(\d+))?까지를\s*각각\s*삭제")
# 재번호 뒤 지칭: 제6조(종전의 제5조)
RE_FORMER = re.compile(r"제\s*(\d+)\s*조(?:의(\d+))?\s*\(\s*종전의\s*제\s*(\d+)\s*조(?:의(\d+))?\s*\)")


def _k(n, b):
    return "%s의%s" % (n, b) if b else str(n)


class Op:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)

    def __repr__(self):
        d = {k: v for k, v in self.__dict__.items() if k != "kind"}
        return "%s(%s)" % (self.kind, ", ".join("%s=%r" % kv for kv in d.items()))


RE_QUOTED = re.compile(r"[%s\"'][^%s\"']*[%s\"']" % (Q1, Q2, Q2))


def _mask(clause):
    """대상 경로 파싱용 — 따옴표 안(치환 문자열)을 지운다."""
    return RE_QUOTED.sub(lambda m: " " * (m.end() - m.start()), clause)


def _targets(clause, last):
    """절에서 대상 경로들을 뽑는다. (조키, 항번호|None, 호번호|None, scope) 목록.

    '제5조제1항 및 제2항' 처럼 복수 대상이 나오므로 목록을 돌려준다.
    '같은 조/같은 항'은 직전 대상(last)에서 상위 성분을 상속한다.
    """
    clause = _mask(clause_raw := clause)
    jo = last.get("jo")
    mf = RE_FORMER.search(clause)
    if mf:
        # `제6조(종전의 제5조)` — 재번호 뒤 번호로 지칭하되 종전 번호를 병기한다.
        # 종전 번호로 찾는 것이 가장 확실하다(재번호가 이미 적용됐는지와 무관).
        jo = "former:" + _k(mf.group(3), mf.group(4))
        hangs = [int(x) for x in RE_HANG_REF.findall(clause[mf.end():])]
        hos = [int(x) for x in RE_HO_REF.findall(clause[mf.end():])]
        scope = ("각호외" if "각 호 외의 부분" in clause else
                 "단서" if "단서" in clause else
                 "전단" if "전단" in clause else
                 "후단" if "후단" in clause else
                 "본문" if "본문" in clause else None)
        out = [(jo, h, None, None) for h in hangs] or [(jo, None, hos[0] if hos else None, None)]
        if scope:
            out[-1] = out[-1][:3] + (scope,)
        return out, {"jo": jo, "hang": hangs[-1] if hangs else None}
    if "같은 조" in clause or "같은 항" in clause or "같은 호" in clause:
        pass                                        # 상속 — jo 유지
    mj = RE_JO_REF.search(clause)
    if mj and not clause.strip().startswith("같은"):
        jo = "%s의%s" % (mj.group(1), mj.group(2)) if mj.group(2) else mj.group(1)
    if jo is None:
        return [], last

    scope = ("각호외" if "각 호 외의 부분" in clause else
             "단서" if "단서" in clause else
             "전단" if "전단" in clause else
             "후단" if "후단" in clause else
             "본문" if "본문" in clause else None)
    hangs = [int(x) for x in RE_HANG_REF.findall(clause)]
    hos = [int(x) for x in RE_HO_REF.findall(clause)]
    if not hangs and "같은 항" in clause and last.get("hang"):
        hangs = [last["hang"]]
    if not hos and "같은 호" in clause and last.get("ho"):
        hos = [last["ho"]]

    # scope(단서·본문)는 **마지막 대상에만** 붙는다.
    # '제7조제1항 및 같은 조 제2항 단서 중' 에서 단서는 제2항의 것이다.
    out = []
    if hangs and hos:
        # `같은 항 제3호 및 제4호를 각각 삭제` — 항이 상속되고 호가 복수인 경우.
        # hos[0] 만 쓰면 제4호가 남아 이후 대비표 행이 전부 밀린다.
        for h in hangs:
            for o in hos:
                out.append((jo, h, o, None))
    elif hangs:
        for h in hangs:
            out.append((jo, h, None, None))
    elif hos:
        for o in hos:
            out.append((jo, last.get("hang"), o, None))
    else:
        out.append((jo, None, None, None))
    if scope and out:
        out[-1] = out[-1][:3] + (scope,)
    last = {"jo": jo, "hang": hangs[-1] if hangs else last.get("hang"),
            "ho": hos[-1] if hos else last.get("ho")}
    return out, last


def compile_ops(entries):
    """개정문 [{level,text}] → ([Op], [경고]).

    '다음과 같이' 가 있는 지시문은 뒤따르는 문단들이 본문 인자다.
    """
    lines = []
    for e in entries:
        t = (e["text"] if isinstance(e, dict) else e or "").strip()
        if t:
            lines.append(t)

    ops, warn, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if RE_BODY_JO.match(line) or RE_HANG.match(line) or RE_HO.match(line):
            continue            # `제18조의2(…) ① …` 는 신설 본문이지 지시문이 아니다
        if not RE_JO_REF.search(line) or not line.rstrip().endswith("한다."):
            continue                                   # 지시문이 아니다(본문은 아래서 흡수)
        body = []
        if "다음과 같이" in line:                       # 뒤따르는 문단이 인자
            while i < len(lines) and not (
                    not (RE_BODY_JO.match(lines[i]) or RE_HANG.match(lines[i])
                         or RE_HO.match(lines[i]))
                    and RE_JO_REF.search(lines[i]) and lines[i].rstrip().endswith("한다.")):
                body.append(lines[i])
                i += 1

        last = {}
        for clause in SPLIT_CLAUSE.split(line):
            clause = clause.strip()
            if not clause:
                continue
            tgts, last = _targets(clause, last)
            if not tgts:
                warn.append("대상 불명: %s" % clause[:50])
                continue

            mt = RE_TITLE_OF.search(clause)
            pairs = RE_PAIR.findall(clause)

            mrr = RE_RANGE_RENUM.search(clause)
            if mrr:
                ops.append(Op("RENUMBER_RANGE",
                              src=(int(mrr.group(1)), int(mrr.group(3))),
                              dst=int(mrr.group(5))))
                if not pairs:
                    continue
            mhr = RE_HO_RANGE_RENUM.search(clause)
            if mhr and tgts:
                ops.append(Op("RENUMBER_HO_RANGE", target=tgts[0],
                              src=(int(mhr.group(1)), int(mhr.group(2))),
                              dst=int(mhr.group(3))))
                continue
            mrd = RE_RANGE_DEL.search(clause)
            if mrd:
                ops.append(Op("DELETE_RANGE", src=(int(mrd.group(1)), int(mrd.group(3)))))
                continue
            if not mrr and not pairs and "신설" not in clause and "다음과 같이" not in clause:
                mor = RE_ONE_RENUM.search(clause)
                if mor:
                    ops.append(Op("RENUMBER", src=_k(mor.group(1), mor.group(2)),
                                  dst=_k(mor.group(3), mor.group(4))))
                    continue

            if mt and pairs:                            # 제목 개정
                ops.append(Op("RETITLE", jo=tgts[0][0], pairs=pairs))
            elif "제목 외의 부분" in clause and "항으로" in clause:
                mh = RE_HANG_REF.search(clause)
                ops.append(Op("PROMOTE", jo=tgts[0][0], hang=int(mh.group(1)) if mh else 1))
            elif "신설" in clause:
                ops.append(Op("INSERT", jo=tgts[0][0], body=body))
            elif "삭제" in clause:
                for t in tgts:
                    ops.append(Op("DELETE", target=t))
            elif "다음과 같이" in clause:                # 전문 개정
                ops.append(Op("REWRITE", jo=tgts[0][0], body=body))
            elif pairs:
                for t in tgts:
                    ops.append(Op("REPLACE", target=t, pairs=pairs))
            else:
                warn.append("해석 불가: %s" % clause[:60])
    return ops, warn


# ── 3단계: 적용기 ───────────────────────────────────────────────────────────
class ApplyError(Exception):
    pass


def _resolve(ix, target):
    """(조키, 항, 호, scope) → Node. 없으면 ApplyError."""
    jo_key, hang, ho, _ = target
    jo = ix.get(jo_key)
    if jo is None:
        raise ApplyError("제%s조가 현행에 없습니다" % jo_key)
    node = jo
    if hang:
        node = jo.find("항", hang)
        if node is None:
            raise ApplyError("제%s조제%d항이 없습니다" % (jo_key, hang))
    if ho:
        node = (node or jo).find("호", ho)
        if node is None:
            raise ApplyError("제%s조%s제%d호가 없습니다"
                             % (jo_key, "제%d항" % hang if hang else "", ho))
    return node


def clone(node, parent=None):
    n = Node(node.level, node.num, node.branch, node.title, node.text, parent)
    n.origin = node.origin
    n.id = node.id                      # **ID를 물려준다** — 좌우 짝짓기의 근거
    n.children = [clone(c, n) for c in node.children]
    return n


def apply_ops(articles, ops, strict=True):
    """현행 AST + 연산 → 개정 후 AST. 원본은 건드리지 않는다.

    **절 순서대로 순차 적용한다.** 자치법규입안길라잡이(2022) 제3편 제2장의
    표준 문형이 재번호 뒤 절에서 **개정 후 번호**로 지칭하기 때문이다:

        제4조부터 제6조까지를 각각 제5조부터 제7조까지로 하고,
        제6조(종전의 제5조) 중 “---”을 “---”로 한다.

    여기서 뒤 절의 '제6조'는 개정 전 제6조가 아니라 방금 재번호된 종전 제5조다.
    `(종전의 제N조)` 병기가 그 대응을 명시하므로, 그 표기가 있으면 **종전 번호로**
    찾는다(재번호 적용 여부와 무관하게 확실하다).
    """
    new = [clone(a) for a in articles]
    errs = []

    def resolve(target):
        jo_key, hang, ho, _ = target
        if str(jo_key).startswith("former:"):
            want = jo_key.split(":", 1)[1]
            jo = next((a for a in new if a.origin == want), None)
            if jo is None:                     # 아직 재번호 전이면 현재 번호로
                jo = index(new).get(want)
        else:
            jo = index(new).get(jo_key)
        if jo is None:
            raise ApplyError("제%s조를 찾을 수 없습니다" % str(jo_key).replace("former:", ""))
        node = jo
        if hang:
            node = jo.find("항", hang)
            if node is None:
                raise ApplyError("제%s조제%d항이 없습니다" % (jo_key, hang))
        if ho:
            node = node.find("호", ho) or jo.find("호", ho)
            if node is None:
                raise ApplyError("제%s조 제%d호가 없습니다" % (jo_key, ho))
        return node

    def renumber(jo, new_num, new_branch=None):
        if jo.origin is None:
            jo.origin = "%d의%d" % (jo.num, jo.branch) if jo.branch else str(jo.num)
        jo.num, jo.branch = new_num, new_branch

    for op in ops:
        try:
            if op.kind == "RENUMBER_RANGE":
                lo, hi = op.src
                delta = op.dst - lo
                for jo in [a for a in new if lo <= a.num <= hi and not a.branch]:
                    renumber(jo, jo.num + delta)

            elif op.kind == "RENUMBER":
                m = re.match(r"(\d+)(?:의(\d+))?$", str(op.src))
                d = re.match(r"(\d+)(?:의(\d+))?$", str(op.dst))
                jo = index(new).get(op.src)
                if jo is None:
                    raise ApplyError("제%s조를 찾을 수 없습니다" % op.src)
                renumber(jo, int(d.group(1)), int(d.group(2)) if d.group(2) else None)

            elif op.kind == "RENUMBER_HO_RANGE":
                node = resolve((op.target[0], op.target[1], None, None))
                lo, hi = op.src
                delta = op.dst - lo
                for ho in [c for c in node.children if c.level == "호" and lo <= c.num <= hi]:
                    if ho.origin is None:
                        ho.origin = str(ho.num)
                    ho.num += delta
                node.children.sort(key=lambda c: (c.level != "호", c.num or 0))

            elif op.kind == "DELETE_RANGE":
                lo, hi = op.src
                for jo in [a for a in new if lo <= a.num <= hi and not a.branch]:
                    new.remove(jo)

            elif op.kind == "REPLACE":
                node = resolve(op.target)
                s0, e0 = scope_span(node, op.target[3])
                head, mid, tail = node.text[:s0], node.text[s0:e0], node.text[e0:]
                for a, b in op.pairs:
                    if a not in mid:
                        msg = "치환 원문 없음: “%s”" % a[:30]
                        if strict:
                            raise ApplyError(msg)
                        errs.append(msg)
                        continue
                    mid = mid.replace(a, b)
                node.text = head + mid + tail

            elif op.kind == "DELETE":
                node = resolve(op.target)
                (node.parent.children if node.parent else new).remove(node)

            elif op.kind == "RETITLE":
                jo = index(new).get(op.jo)
                if jo is None:
                    raise ApplyError("제%s조를 찾을 수 없습니다" % op.jo)
                for a, b in op.pairs:
                    t = "(%s)" % (jo.title or "")
                    if a.strip("()") == (jo.title or "").strip():
                        jo.title = b.strip("()")
                    elif a in t:
                        jo.title = t.replace(a, b).strip("()")
                    elif strict:
                        raise ApplyError("제%s조 제목 불일치: “%s”" % (op.jo, a))

            elif op.kind == "PROMOTE":
                jo = index(new).get(op.jo)
                if jo is None:
                    raise ApplyError("제%s조를 찾을 수 없습니다" % op.jo)
                if jo.text or (jo.children and jo.children[0].level == "호"):
                    h = Node("항", op.hang, text=jo.text, parent=jo)
                    h.promoted = True
                    h.children = jo.children
                    for c in h.children:
                        c.parent = h
                    jo.children = [h]
                    jo.text = ""

            elif op.kind == "INSERT_JO":
                added = parse(op.body)
                if not added:
                    m = re.match(r"(\d+)(?:의(\d+))?", str(op.jo))
                    added = [Node("조", int(m.group(1)),
                                  int(m.group(2)) if m.group(2) else None, None, TODO_NEW)]
                    errs.append("제%s조 신설 본문이 없습니다 — %s 로 표시했습니다"
                                % (op.jo, TODO_NEW))
                for jo in added:
                    pos = next((i for i, x in enumerate(new)
                                if (x.num, x.branch or 0) > (jo.num, jo.branch or 0)), len(new))
                    new.insert(pos, jo)

            elif op.kind in ("INSERT", "REWRITE"):
                jo = index(new).get(op.jo)
                if jo is None and op.kind == "INSERT":
                    op.kind = "INSERT_JO"      # 현행에 없는 조 = 조 자체의 신설
                    ops.append(op)             # 뒤에서 다시 처리
                    continue
                if jo is None:
                    raise ApplyError("제%s조를 찾을 수 없습니다" % op.jo)
                kids = parse_children(op.body, jo)
                if op.kind == "REWRITE":
                    head = parse(op.body)      # 본문에 새 조 머리(제목)가 들어 있다
                    if head and head[0].num == jo.num:
                        jo.title, jo.text = head[0].title, head[0].text
                        jo.children = head[0].children
                    else:
                        jo.children = kids
                    jo.rewritten = True        # 조 전체가 갈렸다 → 대비표에서 1행으로
                else:
                    jo.children.extend(kids)
                    jo.children.sort(key=lambda c: (c.level != "항", c.num or 0))
            else:
                raise ApplyError("모르는 연산: %s" % op.kind)
        except ApplyError as e:
            if strict:
                raise
            errs.append("%s — %s" % (op.kind, e))

    return new, errs


def parse_children(body_lines, parent):
    """신설 본문 줄들 → 자식 Node 목록 (항·호)."""
    out, cur = [], None
    for ln in body_lines:
        ln = (ln or "").strip()
        if not ln:
            continue
        mh, mo = RE_HANG.match(ln), RE_HO.match(ln)
        if mh:
            cur = Node("항", HANG_NUM[mh.group(1)], text=ln[mh.end():].strip(), parent=parent)
            out.append(cur)
        elif mo:
            p = cur or parent
            n = Node("호", int(mo.group(1)), text=ln[mo.end():].strip(), parent=p)
            (p.children if p is not parent else out).append(n)
        elif out:
            out[-1].text = (out[-1].text + " " + ln).strip()
    return out


# ── 4단계: 대비표 렌더러 ────────────────────────────────────────────────────
# 표기 관행은 경기도의회 일부개정조례안 4건(대비표 58행·줄표 2,252자) 실측값이다.
#   · 줄표 문자는 U+002D 반각 하이픈 하나뿐 (전각·다른 대시 0건)
#   · 줄표 개수 = 대체된 원문의 **표시 폭 × 0.85** (중앙 0.844, p10 0.69~p90 0.94)
#   · 상한 없음 (최대 174개 관측). 최소 3
#   · 행 단위는 조가 아니라 **항**. 변경 없는 항도 행을 차지하고 `(생  략)`
#   · 괄호 안 공백 2칸: `(생  략)` `<신  설>` `삭  제`
DASH = "-"
DASH_RATIO = 0.85
DASH_MIN = 3
OMIT_L, OMIT_R = "(생  략)", "(현행과 같음)"
TILDE = "∼"          # 3개 이상 연속 — 실측 U+223C(물결 ~ U+007E 아님)
MIDDOT = "ㆍ"         # 2개 연속 — `①ㆍ②` (실측 19번)


def span(a_mark, b_mark, n):
    """연속 무변경 범위 표기. 2개면 `①ㆍ②`, 3개 이상이면 `① ∼ ③`."""
    return ("%s%s%s" % (a_mark, MIDDOT, b_mark) if n == 2
            else "%s %s %s" % (a_mark, TILDE, b_mark))
# 미완성 자리 — run_suite 의 G3 게이트가 이 토큰을 보고 빌드를 막는다
TODO_NEW = "[[TODO:신설조문]]"
NEW_L, DEL_R = "<신  설>", "<삭  제>"


def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s or "")


def dashes(orig):
    return DASH * max(DASH_MIN, round(width(orig) * DASH_RATIO))


def dash_line(cur, new):
    """현행 문장 → 개정안 문장의 줄표 축약본. 바뀐 부분만 글자로 남긴다.

    **어절 단위로 비교한다.** 문자 단위로 하면 `공동단장`→`단장과 부단장` 에서
    공통 접미사 '단장'이 equal 로 잡혀 `부`만 남는데, 실물 대비표는 바뀐 낱말을
    통째로 적는다(`단장과 부단장-----`). 어절 경계가 실무 표기와 맞다.
    """
    if cur == new:
        return new
    a, b = cur.split(" "), new.split(" ")
    out, buf = [], []

    def flush():
        if buf:
            out.append(dashes(" ".join(buf)))
            buf.clear()

    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            seg = a[i1:i2]
            if width(" ".join(seg)) <= 2:      # '.' 같은 짧은 동일구간은 글자로 남긴다
                flush()
                out.append(" ".join(seg))
            else:
                buf.extend(seg)
        else:
            flush()
            if j1 != j2:
                out.append(" ".join(b[j1:j2]))
    flush()
    return " ".join(x for x in out if x)


def dash_node(cur_node, new_node):
    """마커는 보존하고 **본문만** 줄표로 축약한다.

    실측: 정답 대비표는 `② 단장-----`, `제6조(자료제출 요구 등) 단장----` 처럼
    항 마커·조 머리를 그대로 두고 그 뒤부터 줄표를 시작한다.
    """
    body = dash_line(cur_node.text, new_node.text)
    return (new_node.marker + " " + body).strip()


def _row(cur_node, new_node, head=""):
    """한 행 = (현행, 개정안). head 는 조 머리(첫 항 행에만 붙는다)."""
    c = (head + " " + cur_node.line()).strip() if cur_node else ""
    if cur_node and new_node:
        if cur_node.line() == new_node.line() and head == "":
            return [cur_node.marker + " " + OMIT_L, new_node.marker + " " + OMIT_R]
        n_head = head_of(new_node) if head else ""
        n = (n_head + " " + dash_line(cur_node.line(), new_node.line())).strip() \
            if head else dash_line(cur_node.line(), new_node.line())
        return [c, n]
    if new_node and not cur_node:
        return [NEW_L, (head + " " + new_node.line()).strip()]
    return [c, DEL_R]


def head_of(node):
    """항 노드의 소속 조 머리 표기."""
    return node.parent.marker if node.parent and node.parent.level == "조" else ""


def table(cur_arts, new_arts, style="dash"):
    """현행 AST + 개정 후 AST → 대비표 [[현행, 개정안], …]."""
    by_id = {a.id: a for a in new_arts}
    cur_ids = {a.id for a in cur_arts}
    rows = []
    for a in cur_arts:
        b = by_id.get(a.id)
        if b is None:
            rows.append([a.render(), DEL_R])
            continue
        if a.render() == b.render():
            continue                                  # 변경 없는 조는 대비표에 싣지 않는다
        if getattr(b, "rewritten", False):     # 조 전문개정 — 조 전체를 한 행으로
            rows.append([a.render(), b.render()])
            continue
        hangs_a = [c for c in a.children if c.level == "항"]
        hangs_b = [c for c in b.children if c.level == "항"]

        if not hangs_a and not hangs_b:               # 항이 없는 조 = 조 전체가 1행
            rows.append([a.line(), dash_node(a, b) if style == "dash" else b.line()])
            continue

        bmap = {c.id: c for c in hangs_b}
        seen = set()
        # 연속된 무변경 항은 `① ∼ ③ (생  략)` 로 묶는다(실측: 18번). 연속이 아니면
        # 항마다 별도 행(실측: 21번). 두 샘플을 모두 설명하는 규칙이다.
        i = 0
        while i < len(hangs_a):
            ha = hangs_a[i]
            hb = bmap.get(ha.id)
            head_a = a.marker if i == 0 else ""
            head_b = b.marker if i == 0 else ""
            if hb is None:
                rows.append([(head_a + " " + ha.line()).strip(), DEL_R])
                i += 1
                continue
            seen.add(hb.id)

            if ha.render() == hb.render() and not getattr(hb, "promoted", False):
                j = i
                while (j + 1 < len(hangs_a)):
                    nx = hangs_a[j + 1]
                    nb = bmap.get(nx.id)
                    if nb is None or nx.render() != nb.render():
                        break
                    seen.add(nb.id)
                    j += 1
                if j > i:                                  # 2개 이상 연속 → 범위 묶음
                    n = j - i + 1
                    span_a = span(ha.marker, hangs_a[j].marker, n)
                    span_b = span(bmap[ha.id].marker, bmap[hangs_a[j].id].marker, n)
                    rows.append([(head_a + " " + span_a + " " + OMIT_L).strip(),
                                 (head_b + " " + span_b + " " + OMIT_R).strip()])
                else:
                    rows.append([(head_a + " " + ha.marker + " " + OMIT_L).strip(),
                                 (head_b + " " + hb.marker + " " + OMIT_R).strip()])
                i = j + 1
                continue

            if getattr(hb, "promoted", False):
                rows.append([(head_a + " " + OMIT_L).strip(),
                             (head_b + " " + hb.marker + " (현행 제목 외의 부분과 같음)").strip()])
            else:
                right = dash_node(ha, hb) if style == "dash" else hb.line()
                rows.append([(head_a + " " + ha.line()).strip(),
                             (head_b + " " + right).strip()])
            rows.extend(_child_rows(ha, hb, style))
            i += 1
        for hb in hangs_b:                            # 신설된 항
            if hb.id in seen:
                continue
            if getattr(hb, "promoted", False):
                rows.append([(a.marker + " " + OMIT_L).strip(),
                             (b.marker + " " + hb.marker + " (현행 제목 외의 부분과 같음)").strip()])
            else:
                rows.append([NEW_L, hb.line()])
    for b in new_arts:                                # 신설된 조
        if b.id not in cur_ids:
            rows.append([NEW_L, b.render()])
    return rows


def _child_rows(ha, hb, style):
    """항 아래 호(號) 행. 실측(19번): 무변경 호도 행으로 싣고 `1. (생  략)`,
    연속이면 `1. ∼ 4. (생  략)` 로 묶는다 — 항과 같은 규칙이다."""
    out = []
    kids_a = [c for c in ha.children if c.level == "호"]
    bm = {c.id: c for c in hb.children if c.level == "호"}
    seen, i = set(), 0
    while i < len(kids_a):
        ka = kids_a[i]
        kb = bm.get(ka.id)
        if kb is None:
            out.append([ka.line(), DEL_R])
            i += 1
            continue
        seen.add(kb.id)
        if ka.render() == kb.render():
            j = i
            while j + 1 < len(kids_a):
                nx = kids_a[j + 1]
                nb = bm.get(nx.id)
                if nb is None or nx.render() != nb.render():
                    break
                seen.add(nb.id)
                j += 1
            if j > i:
                n = j - i + 1
                out.append([span(ka.marker, kids_a[j].marker, n) + " " + OMIT_L,
                            span(bm[ka.id].marker, bm[kids_a[j].id].marker, n) + " " + OMIT_R])
            else:
                out.append([ka.marker + " " + OMIT_L, kb.marker + " " + OMIT_R])
            i = j + 1
            continue
        out.append([ka.line(), dash_node(ka, kb) if style == "dash" else kb.line()])
        i += 1
    for kb in [c for c in hb.children if c.level == "호"]:
        if kb.id not in seen:
            out.append([NEW_L, kb.line()])
    return out


def _pair_children(a, b):
    bm = {c.id: c for c in b.children}
    out = [(c, bm.get(c.id)) for c in a.children]
    am = {c.id for c in a.children}
    out += [(None, c) for c in b.children if c.id not in am]
    return out
