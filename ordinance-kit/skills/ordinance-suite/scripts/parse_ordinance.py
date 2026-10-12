# -*- coding: utf-8 -*-
"""조례안 파일(.hwpx/.txt/.md) → 통합 스펙(suite.json) 추출기.

의안 서식 완성본이면 제안이유·주요내용·참고사항·발의자까지 뽑고, 조문만 있는
파일이면 조문 계층만 뽑는다. 추출은 하되 **없는 값을 지어내지 않는다** — 못 채운
칸은 비워두거나 눈에 보이는 자리표시자(○○○)로 두고 `_미확정` 목록에 남겨 호출자가
사용자에게 묻도록 한다.

2026-10-12 감사 반영:
  - 부칙은 붙임 경계(별표·별지·붙임·비용추계·관계법령 발췌·신구조문대비표·
    검토의견·입법예고·참고 N)에서 멈춘다. HWPX 표 안 글자는 본문으로 읽지 않는다.
  - 개정조례: 두문(「…」 일부/전부를 다음과 같이 개정한다.)에서 `현행조례명`을 뽑고,
    두문 **다음 줄부터 부칙 전까지**를 `개정문`으로 보존한다(괄호 없는 지시문 포함).
  - 장·절 제목은 각각 독립 항목(level 장·절)이다. 제1조 앞의 제1장+제1절 모두 보존.
  - 유형은 ordtype.detect_type 공용 규칙.

usage:
    python parse_ordinance.py 조례안.hwpx suite.json
    python parse_ordinance.py 조례안.hwpx suite.json --대표발의 홍길동
"""
import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ordtype import TypeConflict, detect_type, is_amend  # noqa: E402

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
Q = lambda n: "{%s}%s" % (HP, n)

RE_PROM = re.compile(r"^(\S+)\s*조례\s*제\s*\d*\s*호\s*$")      # 경기도 조례  제   호
RE_JO = re.compile(r"^제\d+조(?:의\d+)?\s*[(（]")
RE_SUPP = re.compile(r"^부\s*칙\s*$")
# 개정 두문: 「경기도 ○○ 조례」 일부를 다음과 같이 개정한다.
RE_DUMUN = re.compile(r"^\s*「?\s*(.+?)\s*」?\s*(일부|전부)\s*를\s*다음과\s*같이\s*개정한다\.?\s*$")
LEVELS = [
    ("장", re.compile(r"^제\d+장(?:\s|$)")),          # 장 제목(2026-09-14)
    ("절", re.compile(r"^제\d+절(?:\s|$)")),          # 절 제목(2026-10-12)
    ("조", re.compile(r"^제\d+조(?:의\d+)?")),
    ("항", re.compile(r"^[①-⑳]")),
    ("(가)", re.compile(r"^\([가-힣]\)")),
    ("(1)", re.compile(r"^\(\d+\)")),
    ("가)", re.compile(r"^[가-힣]\)")),
    ("1)", re.compile(r"^\d+\)")),
    ("호", re.compile(r"^\d+\.")),
    ("목", re.compile(r"^[가-힣]\.")),
]
HEADINGS = ("장", "절")

# ── 붙임 경계: 부칙(또는 본칙) 수집을 여기서 멈춘다 ─────────────────────
# 조문 마커로 시작하는 줄은 경계가 아니다(부칙 조문이 "별표"를 언급할 수 있다).
RE_ATTACH = [
    re.compile(r"^\s*\[?\s*별\s*표"),
    re.compile(r"^\s*\[?\s*별\s*지"),
    re.compile(r"^\s*\[?\s*(?:덧)?붙\s*임"),
    re.compile(r"비용\s*추계"),
    re.compile(r"관계\s*법령\s*발췌"),
    re.compile(r"신\s*[·ㆍ]?\s*구\s*조\s*문"),
    re.compile(r"검토\s*의견"),
    re.compile(r"^\s*입법\s*예고"),
    re.compile(r"^\s*\[?\s*참고\s*\d"),
]
SUPP_WARN = 20

# 문의처 자리표시자 — 실제 번호처럼 보이는 가짜 기본값(031)8008-0000)을 쓰지 않는다.
# 눈에 보이는 ○ 표시로 두고 `_미확정`에 올린다.
PH_CONTACT = "○○○과 031)8008-○○○○"


def level_of(line):
    s = line.strip()
    for name, rx in LEVELS:
        if rx.match(s):
            return name
    return None


def is_attach(line):
    s = line.strip()
    if not s:
        return False
    if RE_ATTACH[0].match(s) or RE_ATTACH[1].match(s):
        return True
    if level_of(s) in ("조", "항", "호", "목"):
        return False
    return any(rx.search(s) for rx in RE_ATTACH[2:])


def _para_text(p):
    """문단 글자 — 문단 안에 든 표(hp:tbl)의 글자는 제외한다."""
    out = []
    for t in p.iter(Q("t")):
        if any(a.tag == Q("tbl") for a in t.iterancestors()):
            continue
        out.append("".join(t.itertext()))
    return "".join(out)


def read_paragraphs(path):
    """파일 → (본문 문단 텍스트 목록, 표 글자 목록).

    표 글자(의안 표지의 발의연월일·발의자, 붙임의 대비표 등)는 본문 순서에 넣지
    않는다 — 메타데이터 검색에만 쓴다."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".hwpx":
        from lxml import etree
        out, tbls = [], []
        with zipfile.ZipFile(path) as z:
            secs = sorted((n for n in z.namelist()
                           if re.match(r"Contents/section\d+\.xml$", n)),
                          key=lambda n: int(re.search(r"(\d+)", n.split("/")[1]).group(1)))
            for n in secs:
                root = etree.fromstring(z.read(n))
                for p in root:
                    if p.tag != Q("p"):
                        continue
                    out.append(_para_text(p))
                    for tbl in p.iter(Q("tbl")):
                        tbls.append("".join(tbl.itertext()))
        return out, tbls
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines(), []
    raise SystemExit("지원하지 않는 형식: %s (hwpx·txt·md만)" % ext)


def find(lines, rx, start=0, stop=None):
    for i in range(max(start, 0), len(lines) if stop is None else stop):
        if rx.match(lines[i].strip()):
            return i
    return -1


def find_attach(lines, start):
    for i in range(max(start, 0), len(lines)):
        if is_attach(lines[i]):
            return i
    return len(lines)


def collect_body(lines, a, b, first_default=None):
    """lines[a:b] → [{level,text}] (계층 미상 줄은 앞 항목에 이어붙인다 — 단 장·절
    제목 뒤에는 붙이지 않는다). first_default: 첫 줄이 번호 없는 문장이면 이 레벨로
    채택(부칙 "이 조례는 공포한 날부터 시행한다." 구제)."""
    out = []
    for raw in lines[a:b]:
        s = raw.strip()
        if not s:
            continue
        lv = level_of(s)
        if lv is None:
            if out and out[-1]["level"] not in HEADINGS:
                out[-1]["text"] += " " + s
            elif first_default:
                out.append({"level": first_default, "text": s})
            continue
        out.append({"level": lv, "text": s})
    return out


def collect_amend(lines, a, b):
    """개정문 수집 — 두문 다음 줄부터. 지시문은 문단 하나가 한 항목이다.
    괄호 없는 지시문(`제3조제2항 중 “3년”을 “5년”으로 한다.`)·`같은 조 …`처럼
    번호 마커가 없는 지시문도 **독립 항목(level 조)** 으로 남긴다."""
    out = []
    for raw in lines[a:b]:
        s = raw.strip()
        if not s:
            continue
        lv = level_of(s) or "조"
        out.append({"level": lv, "text": s})
    return out


def back_over_headings(lines, start, floor):
    """제1조 바로 앞의 장·절 제목 줄(여러 줄)을 본칙에 포함시킨다."""
    j = start - 1
    while j > floor:
        s = lines[j].strip()
        if not s:
            j -= 1
            continue
        if level_of(s) in HEADINGS:
            start = j
            j -= 1
            continue
        break
    return start


def strip_title_suffix(title):
    return re.sub(r"\s*(?:일부\s*개정|전부\s*개정|개정)\s*(?:조례|규칙)안?\s*$", "", title).strip()


def parse(path, overrides):
    lines, tbls = read_paragraphs(path)
    txt = "\n".join(lines + tbls)
    spec, unknown, notes = {}, [], []

    # ── 제명 ──────────────────────────────────────────────────────
    title = next((l.strip() for l in lines[:8]
                  if l.strip().endswith(("조례안", "조례", "규칙안"))), "")
    if not title:
        title = next((l.strip() for l in lines if l.strip().endswith(("조례안", "규칙안"))), "")
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
        unknown.append("조례명")
    title = overrides.get("조례명") or title
    spec["조례명"] = re.sub(r"[「」]", "", title).strip()

    # ── 유형 · 공포기관 ───────────────────────────────────────────
    try:
        spec["유형"] = detect_type(spec["조례명"], overrides.get("유형"))
    except TypeConflict as e:
        raise SystemExit(str(e))
    amend = is_amend(spec["유형"])
    i_prom = find(lines, RE_PROM)
    spec["공포기관"] = (RE_PROM.match(lines[i_prom].strip()).group(1) if i_prom >= 0
                        else ("경기도교육청" if spec["조례명"].startswith("경기도교육청")
                              else "경기도"))

    # ── 발의 정보 ─────────────────────────────────────────────────
    m = re.search(r"[(（]\s*([^()（）]+?)\s*의원\s*대표발의\s*[)）]", txt)
    if m:
        spec["대표발의"] = m.group(1).strip()
    else:
        spec["대표발의"] = ""
        unknown.append("대표발의")
    m = re.search(r"발의연월일\s*[:：]\s*([0-9]{2,4}\s*년[^발]*?)(?=발|$)", txt)
    spec["발의연월일"] = m.group(1).strip() if m else "    년    월    일"
    m = re.search(r"발\s*의\s*자\s*[:：]\s*(.+)", txt)
    spec["발의자"] = m.group(1).strip() if m else spec["대표발의"]

    # ── 제안설명서 ────────────────────────────────────────────────
    i_r = find(lines, re.compile(r"^1\.\s*제안이유"))
    i_m = find(lines, re.compile(r"^2\.\s*주요내용"))
    i_n = find(lines, re.compile(r"^3\.\s*참고사항"))
    end = i_prom if i_prom >= 0 else len(lines)
    if i_r >= 0:
        stop = i_m if i_m > 0 else (i_n if i_n > 0 else end)
        spec["제안이유"] = [l.strip() for l in lines[i_r + 1:stop] if l.strip()]
    else:
        spec["제안이유"] = []
        unknown.append("제안이유")
    if i_m >= 0:
        stop = i_n if i_n > 0 else end
        spec["주요내용"] = [l.strip() for l in lines[i_m + 1:stop] if l.strip()]
    else:
        spec["주요내용"] = []
        unknown.append("주요내용")
    if i_n >= 0:
        spec["참고사항"] = [l.strip() for l in lines[i_n + 1:end] if l.strip()]

    # ── 본칙 · 부칙 ───────────────────────────────────────────────
    floor = i_prom if i_prom >= 0 else max(i_n, i_m, i_r, 0)
    i_supp = find(lines, RE_SUPP, floor + 1)
    if amend:
        stop = i_supp if i_supp > 0 else find_attach(lines, floor + 1)
        i_d = find(lines, RE_DUMUN, floor + 1, stop)
        if i_d < 0:
            i_d = find(lines, RE_DUMUN, 0, stop)
        if i_d >= 0:
            md = RE_DUMUN.match(lines[i_d].strip())
            spec["현행조례명"] = re.sub(r"[「」]", "", md.group(1)).strip()
            kind = "전부개정" if md.group(2) == "전부" else "일부개정"
            if kind != spec["유형"]:
                notes.append("두문은 '%s'인데 유형은 '%s' — 확인 필요" % (md.group(2), spec["유형"]))
            body_from = i_d + 1
        else:
            # 두문이 없는 파일(지시문만): 첫 지시문부터
            body_from = -1
            for i in range(floor + 1, stop):
                s = lines[i].strip()
                if level_of(s) == "조" and s.endswith("한다."):
                    body_from = i
                    break
            spec["현행조례명"] = strip_title_suffix(spec["조례명"])
            unknown.append("현행조례명")
            notes.append("개정 두문을 찾지 못해 현행조례명을 제명에서 추정했습니다: 「%s」"
                         % spec["현행조례명"])
        if body_from < 0:
            raise SystemExit("개정문(두문 또는 지시문)을 찾지 못했습니다: %s" % path)
        spec["개정문"] = collect_amend(lines, body_from, stop)
        if not spec["개정문"]:
            raise SystemExit("두문 뒤에 개정 지시문이 없습니다: %s" % path)
        spec["본칙"] = list(spec["개정문"])        # 하위호환(공유 계약): 본칙 = 개정문
    else:
        start = find(lines, RE_JO, floor + 1)
        if start < 0:
            start = find(lines, RE_JO)
        if start < 0:
            if find(lines, RE_DUMUN) >= 0:
                raise SystemExit("조문(제1조…)이 없고 개정 두문이 있습니다 — 개정조례라면 "
                                 "--유형 일부개정|전부개정 으로 지정하십시오: %s" % path)
            raise SystemExit("조문(제1조…)을 찾지 못했습니다: %s" % path)
        start = back_over_headings(lines, start, floor)
        if i_supp < start:
            i_supp = find(lines, RE_SUPP, start)
        body_end = i_supp if i_supp > 0 else find_attach(lines, start)
        spec["본칙"] = collect_body(lines, start, body_end)

    if i_supp > 0:
        supp_end = find_attach(lines, i_supp + 1)
        spec["부칙"] = collect_body(lines, i_supp + 1, supp_end, first_default="조")
        if supp_end < len(lines):
            notes.append("부칙 수집을 붙임 경계에서 멈췄습니다: %r" % lines[supp_end].strip()[:40])
        if len(spec["부칙"]) > SUPP_WARN:
            notes.append("부칙이 %d문단입니다 — 붙임이 섞였는지 확인하십시오" % len(spec["부칙"]))
    else:
        spec["부칙"] = [{"level": "조", "text": "이 조례는 공포한 날부터 시행한다."}]
        unknown.append("부칙")

    # ── 서식별 빈칸(사용자 확인 대상) ─────────────────────────────
    spec.setdefault("소관위원회", "")
    spec.setdefault("공고번호", "제    -    호")
    spec.setdefault("공고일", "    .   .   .")
    spec.setdefault("제출기한", "    년   월   일(  )까지")
    spec.setdefault("문의처", PH_CONTACT)
    spec.setdefault("관계법령", [])
    spec.setdefault("공동발의", [])

    spec.update(overrides)
    for k in ("소관위원회", "공고번호", "공고일", "제출기한", "문의처"):
        v = str(spec.get(k) or "")
        if not v.strip() or "    " in v or "○" in v:
            unknown.append(k)
    if not spec["관계법령"]:
        unknown.append("관계법령")
    if not spec["공동발의"]:
        unknown.append("공동발의")
    # 표시 라벨이 아니라 **스펙 키** 를 담는다 — 나중에 값이 채워졌는지
    # 그대로 대조할 수 있어야 한다(라벨을 담으면 키와 안 맞아 오보고된다).
    seen = set()
    spec["_미확정"] = [k for k in unknown if not (k in seen or seen.add(k))]
    if notes:
        spec["_경고"] = notes
    spec["_원본"] = os.path.abspath(path)
    return spec


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    for k in ("조례명", "유형", "현행조례명", "대표발의", "소관위원회", "공포기관",
              "발의연월일", "발의자", "문의처"):
        ap.add_argument("--" + k, dest=k)
    a = ap.parse_args()
    ov = {k: v for k, v in vars(a).items()
          if k not in ("src", "out") and v is not None}
    s = parse(a.src, ov)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    print("조례명: %s (%s)" % (s["조례명"], s["유형"]))
    if is_amend(s["유형"]):
        print("현행조례명: %s / 개정문 %d문단" % (s.get("현행조례명", ""), len(s.get("개정문", []))))
    print("본칙 %d문단 / 부칙 %d문단 / 제안이유 %d · 주요내용 %d"
          % (len(s["본칙"]), len(s["부칙"]), len(s["제안이유"]), len(s["주요내용"])))
    for n in s.get("_경고", []):
        print("!! %s" % n)
    print("미확정: %s" % (", ".join(s["_미확정"]) or "없음"))
    print("spec: %s" % a.out)
