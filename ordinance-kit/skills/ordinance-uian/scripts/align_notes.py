# -*- coding: utf-8 -*-
"""참고사항 점선 정렬기 — com_save.py 가 만든 검증 PDF 로 `[붙임 N]` 의 오른쪽 끝 위치를 재서
스테이징 HWPX 의 하이픈 개수를 보정한다(하이픈 1개 = 7.001pt, 본문 14pt 기준).

기준선(실물 완성본)은 다./라./마. 줄의 `[붙임 N]` 오른쪽 끝이 본문 오른쪽 여백(x≈538pt)에
맞춰져 있다. 빌더 기본값은 하이픈 수가 고정이라 문서번호·일자 길이에 따라 어긋난다.

usage:
    python align_notes.py stage.hwpx verify.pdf [--right 538] [--out stage.hwpx]
흐름: build_uian → com_save(stage→out, pdf) → align_notes(stage, pdf) → com_save 다시.
      보정 0건이면 exit 0 과 "정렬 완료", 보정이 있으면 exit 1 과 재저장 안내를 낸다(최대 2회 반복).
규칙(실측 2026-09-14): 오른쪽 여백을 0.7pt만 넘어도 `N]`이 다음 줄로 떨어진다 — 하이픈 추가는
      내림(floor)으로만 계산하고, 라벨이 두 줄로 갈라졌으면 하이픈 1개를 뺀다. 줄 전체가 넘쳐
      머리(다./라./마.)와 다른 줄에 있으면 5개를 먼저 빼고 다시 잰다.
"""
import argparse
import math
import os
import re
import sys
import zipfile

import fitz  # pymupdf
from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
NS = {"hp": HP}
HYPHEN_PT = 7.001
NUM = re.compile(r"^(\d+)\]$")


def measure(pdf, right):
    """PDF 에서 `[붙임 N]` 마다 {N: (delta_hyphens, kind, x1)} — kind: ok/split/overflow."""
    doc = fitz.open(pdf)
    out = {}
    for page in doc:
        words = page.get_text("words")            # x0,y0,x1,y1,text,block,line,word
        for w in words:
            if not w[4].startswith("[붙임"):
                continue
            y = (w[1] + w[3]) / 2
            line = sorted([v for v in words if abs((v[1] + v[3]) / 2 - y) < 3], key=lambda v: v[0])
            after = [v for v in line if v[0] > w[0]]
            num = next((v for v in after if NUM.match(v[4])), None)
            head = any(re.match(r"^[가-힣]\.$", v[4]) for v in line)
            if num is not None and head:                       # 정상: 같은 줄에 머리·라벨 전부
                x1 = num[2]
                delta = int(math.floor((right - x1) / HYPHEN_PT))   # 넘치면 안 되므로 내림
                out[NUM.match(num[4]).group(1)] = (delta, "ok", round(x1, 1))
                continue
            # 라벨이 갈라졌거나 줄이 넘친 경우 — 다음 줄에서 번호를 찾는다
            below = sorted([v for v in words if y + 5 < (v[1] + v[3]) / 2 < y + 40], key=lambda v: (v[1], v[0]))
            numb = next((v for v in below if NUM.match(v[4])), None)
            n = NUM.match(numb[4]).group(1) if numb else None
            if n is None:
                continue
            if not head:
                out[n] = (-5, "overflow", round(w[2], 1))
            else:
                out[n] = (-1, "split", round(w[2], 1))
    doc.close()
    return out


def adjust(stage, out, meas):
    z = zipfile.ZipFile(stage)
    order = z.namelist()
    items = {n: z.read(n) for n in order}
    z.close()
    s0 = etree.fromstring(items["Contents/section0.xml"])
    changed = []
    for p in s0.findall(".//hp:p", NS):
        txt = "".join(p.itertext())
        m = re.search(r"\[붙임\s*(\d+)\]", txt)
        if not m or m.group(1) not in meas:
            continue
        delta, kind, x1 = meas[m.group(1)]
        if delta == 0:
            continue
        for t in p.iter("{%s}t" % HP):
            if t.text and "---" in t.text:
                run = re.search(r"-{3,}", t.text)
                n = max(3, len(run.group(0)) + delta)
                t.text = t.text[:run.start()] + "-" * n + t.text[run.end():]
                for lsa in p.findall("{%s}linesegarray" % HP):
                    p.remove(lsa)
                changed.append((m.group(1), delta, n, kind, x1))
                break
    if not changed:
        return []
    items["Contents/section0.xml"] = etree.tostring(s0, encoding="UTF-8", xml_declaration=True, standalone=True)
    if os.path.exists(out):
        os.remove(out)
    zo = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
    zi = zipfile.ZipInfo("mimetype"); zi.compress_type = zipfile.ZIP_STORED
    zo.writestr(zi, items["mimetype"])
    for n in order:
        if n != "mimetype":
            zo.writestr(n, items[n])
    zo.close()
    return changed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("stage"); ap.add_argument("pdf")
    ap.add_argument("--right", type=float, default=538.0, help="본문 오른쪽 끝 x(pt)")
    ap.add_argument("--out", default=None, help="보정본 경로(기본: stage 덮어쓰기)")
    a = ap.parse_args()
    meas = measure(a.pdf, a.right)
    if not meas:
        print("PDF 에서 [붙임 N] 을 찾지 못했습니다(참고사항 없음 또는 렌더 실패)"); sys.exit(0)
    for k, (d, kind, x1) in sorted(meas.items()):
        print("[붙임 %s] x1=%.1f  목표≤%.1f  보정=%+d하이픈  (%s)" % (k, x1, a.right, d, {"ok": "정상", "split": "라벨 줄갈림", "overflow": "줄넘침"}[kind]))
    ch = adjust(a.stage, a.out or a.stage, meas)
    if ch:
        for k, d, n, kind, x1 in ch:
            print("  → [붙임 %s] 하이픈 %+d → %d개" % (k, d, n))
        print("보정본 저장: %s — com_save.py 로 다시 저장한 뒤 align_notes 를 한 번 더 돌려 0건인지 확인" % (a.out or a.stage))
        sys.exit(1)
    print("정렬 완료 — 보정 0건")
