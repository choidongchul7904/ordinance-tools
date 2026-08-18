# -*- coding: utf-8 -*-
"""조례안 파일(.hwpx/.txt/.md) → 통합 스펙(suite.json) 추출기.

의안 서식 완성본이면 제안이유·주요내용·참고사항·발의자까지 뽑고, 조문만 있는
파일이면 조문 계층만 뽑는다. 추출은 하되 **없는 값을 지어내지 않는다** — 못 채운
칸은 비워두고 `_미확정` 목록에 남겨 호출자가 사용자에게 묻도록 한다.

usage:
    python parse_ordinance.py 조례안.hwpx suite.json
    python parse_ordinance.py 조례안.hwpx suite.json --대표발의 윤도희
"""
import argparse
import json
import os
import re
import sys
import zipfile

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
Q = lambda n: "{%s}%s" % (HP, n)

RE_PROM = re.compile(r"^(\S+)\s*조례\s*제\s*\d*\s*호\s*$")      # 경기도 조례  제   호
RE_JO = re.compile(r"^제\d+조(?:의\d+)?\s*[(（]")
RE_SUPP = re.compile(r"^부\s*칙\s*$")
LEVELS = [
    ("조", re.compile(r"^제\d+조(?:의\d+)?")),
    ("항", re.compile(r"^[①-⑳]")),
    ("(가)", re.compile(r"^\([가-힣]\)")),
    ("(1)", re.compile(r"^\(\d+\)")),
    ("가)", re.compile(r"^[가-힣]\)")),
    ("1)", re.compile(r"^\d+\)")),
    ("호", re.compile(r"^\d+\.")),
    ("목", re.compile(r"^[가-힣]\.")),
]


def level_of(line):
    s = line.strip()
    for name, rx in LEVELS:
        if rx.match(s):
            return name
    return None


def read_paragraphs(path):
    """파일 → 문단 텍스트 목록(원문 순서)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".hwpx":
        out = []
        with zipfile.ZipFile(path) as z:
            secs = sorted((n for n in z.namelist()
                           if re.match(r"Contents/section\d+\.xml$", n)),
                          key=lambda n: int(re.search(r"(\d+)", n.split("/")[1]).group(1)))
            for n in secs:
                root = etree.fromstring(z.read(n))
                for p in root:
                    if p.tag == Q("p"):
                        out.append("".join(p.itertext()))
        return out
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    raise SystemExit("지원하지 않는 형식: %s (hwpx·txt·md만)" % ext)


def find(lines, rx, start=0):
    for i in range(start, len(lines)):
        if rx.match(lines[i].strip()):
            return i
    return -1


def collect_body(lines, a, b):
    """lines[a:b] → [{level,text}] (계층 미상 줄은 앞 항목에 이어붙인다)"""
    out = []
    for raw in lines[a:b]:
        s = raw.strip()
        if not s:
            continue
        lv = level_of(s)
        if lv is None:
            if out:
                out[-1]["text"] += " " + s
            continue
        out.append({"level": lv, "text": s})
    return out


def parse(path, overrides):
    lines = read_paragraphs(path)
    txt = "\n".join(lines)
    spec, unknown = {}, []

    # ── 제명 ──────────────────────────────────────────────────────
    title = next((l.strip() for l in lines[:8]
                  if l.strip().endswith(("조례안", "조례"))), "")
    if not title:
        title = next((l.strip() for l in lines if l.strip().endswith("조례안")), "")
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
        unknown.append("조례명(파일명에서 추정)")
    spec["조례명"] = re.sub(r"[「」]", "", title)

    # ── 유형 · 공포기관 ───────────────────────────────────────────
    spec["유형"] = ("전부개정" if "전부개정" in title else
                    "일부개정" if "일부개정" in title else "제정")
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
        unknown.append("대표발의 의원")
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
    start = find(lines, RE_JO, i_prom + 1 if i_prom >= 0 else max(i_n, i_m, i_r, 0) + 1)
    if start < 0:
        start = find(lines, RE_JO)
    if start < 0:
        raise SystemExit("조문(제1조…)을 찾지 못했습니다: %s" % path)
    i_supp = find(lines, RE_SUPP, start)
    spec["본칙"] = collect_body(lines, start, i_supp if i_supp > 0 else len(lines))
    spec["부칙"] = (collect_body(lines, i_supp + 1, len(lines)) if i_supp > 0 else
                    [{"level": "조", "text": "이 조례는 공포한 날부터 시행한다."}])

    # ── 서식별 빈칸(사용자 확인 대상) ─────────────────────────────
    spec.setdefault("소관위원회", "")
    spec.setdefault("공고번호", "제    -    호")
    spec.setdefault("공고일", "    .   .   .")
    spec.setdefault("제출기한", "    년   월   일(  )까지")
    spec.setdefault("문의처", "")
    spec.setdefault("관계법령", [])
    spec.setdefault("공동발의", [])
    for k in ("소관위원회", "공고번호", "공고일", "제출기한", "문의처"):
        if not spec[k] or "    " in str(spec[k]):
            unknown.append(k)
    if not spec["관계법령"]:
        unknown.append("관계법령 발췌(입법예고 참고자료)")
    if not spec["공동발의"]:
        unknown.append("공동발의 의원 명단(서명부)")

    spec.update(overrides)
    spec["_미확정"] = unknown
    spec["_원본"] = os.path.abspath(path)
    return spec


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    for k in ("조례명", "대표발의", "소관위원회", "공포기관", "발의연월일", "발의자"):
        ap.add_argument("--" + k, dest=k)
    a = ap.parse_args()
    ov = {k: v for k, v in vars(a).items()
          if k not in ("src", "out") and v is not None}
    s = parse(a.src, ov)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    print("조례명: %s (%s)" % (s["조례명"], s["유형"]))
    print("본칙 %d문단 / 부칙 %d문단 / 제안이유 %d · 주요내용 %d"
          % (len(s["본칙"]), len(s["부칙"]), len(s["제안이유"]), len(s["주요내용"])))
    print("미확정: %s" % (", ".join(s["_미확정"]) or "없음"))
    print("spec: %s" % a.out)
