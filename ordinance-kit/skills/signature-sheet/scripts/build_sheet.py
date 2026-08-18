# -*- coding: utf-8 -*-
"""안건 서명부(발의의원 서명부) HWPX 생성기.

내장 서식(assets/template.hwpx)의 '페이지 블록'(제목·부제·안건명·표 = 문단 6개)을
필요한 쪽수만큼 복제하거나 지워서 인원수에 맞춘 서명부를 만든다.
표는 언제나 16행(헤더 1 + 데이터 15)을 유지하므로 원본 레이아웃이 그대로 보존되고,
남는 행은 손서명용 빈칸으로 남는다.

출력은 '스테이징' HWPX다 — 반드시 com_save.py로 한/글 정품 재저장을 거쳐야
보안수준 '높음'에서 열린다.

usage:
    python build_sheet.py config.json stage.hwpx [--template PATH]

config.json:
    {
      "안건명":   "경기도 ○○ 조례 일부개정조례안",
      "대표발의": "홍길동",
      "제목":     "발의의원 서명부",          # 선택, 기본값 그대로
      "pages":    3,                          # 선택, 미지정 시 인원수로 자동 산정
      "members": [ {"소속": "수원1", "성명": "김철수"}, "성남2|이영희" ]
    }
"""
import argparse
import json
import math
import os
import shutil
import sys
import zipfile

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
Q = lambda n: "{%s}%s" % (HP, n)

ROWS_PER_PAGE = 15      # 표 데이터행 수 (헤더 제외)
BLOCK_LEN = 6           # 페이지 1개 = 문단 6개
COL_NO, COL_ORG, COL_NAME, COL_SIGN = 0, 1, 2, 3


# ---------------------------------------------------------------- 저수준 헬퍼
def drop_lsa(p):
    """레이아웃 캐시 제거 — 남겨두면 재저장 후 글자가 한 줄에 겹친다."""
    for lsa in p.findall(Q("linesegarray")):
        p.remove(lsa)


def drop_lsa_row(tr):
    """행 높이가 바뀔 수 있으므로 글자를 안 바꾼 셀까지 행 전체를 지운다."""
    for p in tr.iter(Q("p")):
        drop_lsa(p)


def set_cell_text(tc, text):
    """셀 텍스트 교체. 빈 run(<hp:run/>)에는 hp:t를 새로 만들어 넣는다."""
    p = tc.find(Q("subList")).find(Q("p"))
    for i, run in enumerate(p.findall(Q("run"))):
        ts = run.findall(Q("t"))
        if i == 0:
            if ts:
                ts[0].text = text
                for extra in ts[1:]:
                    run.remove(extra)
            else:
                etree.SubElement(run, Q("t")).text = text
        else:
            for t in ts:
                run.remove(t)
    drop_lsa(p)


def set_para_text(p, slot, text):
    """본문 문단에서 '글자를 가진 run' 중 slot번째를 교체한다.

    1쪽 첫 문단에는 secPr를 담은 빈 run이 앞에 붙어 있어 run 인덱스가 쪽마다
    다르다 — hp:t를 가진 run만 세어야 쪽과 무관하게 같은 자리를 가리킨다.
    """
    runs = [r for r in p.findall(Q("run")) if r.find(Q("t")) is not None]
    if slot >= len(runs):
        raise SystemExit("문단 슬롯 %d 없음 (글자 run %d개)" % (slot, len(runs)))
    ts = runs[slot].findall(Q("t"))
    ts[0].text = text
    for extra in ts[1:]:
        runs[slot].remove(extra)
    drop_lsa(p)


def normalize_members(raw):
    out = []
    for m in raw or []:
        if isinstance(m, str):
            parts = [x.strip() for x in m.split("|")]
            org, name = (parts + ["", ""])[:2]
        else:
            org = (m.get("소속") or m.get("org") or "").strip()
            name = (m.get("성명") or m.get("의원명") or m.get("name") or "").strip()
        out.append((org, name))
    return out


# ---------------------------------------------------------------- 본 처리
def build(cfg, template, out_path):
    work = out_path + ".unz"
    if os.path.isdir(work):
        shutil.rmtree(work)
    with zipfile.ZipFile(template) as z:
        z.extractall(work)
        names = z.namelist()

    sec_path = os.path.join(work, "Contents", "section0.xml")
    tree = etree.parse(sec_path)
    sec = tree.getroot()
    if len(sec) != BLOCK_LEN * 2:
        raise SystemExit("서식 구조가 예상과 다름: 문단 %d개 (기대 12개)" % len(sec))

    members = normalize_members(cfg.get("members"))
    pages = cfg.get("pages")
    if not pages:
        pages = max(1, math.ceil(len(members) / ROWS_PER_PAGE)) if members else 2
    if pages * ROWS_PER_PAGE < len(members):
        raise SystemExit("pages=%d 로는 %d명을 담을 수 없음" % (pages, len(members)))

    block2 = list(sec)[BLOCK_LEN:]      # secPr가 없는 깨끗한 복제 원본(2쪽)

    if pages == 1:
        for p in block2:
            sec.remove(p)
    else:
        for _ in range(pages - 2):
            for p in block2:
                sec.append(etree.fromstring(etree.tostring(p)))

    # 복제로 중복된 표 id 재발급
    max_id = 0
    for tbl in sec.iter(Q("tbl")):
        try:
            max_id = max(max_id, int(tbl.get("id") or 0))
        except ValueError:
            pass
    seen = set()
    for tbl in sec.iter(Q("tbl")):
        if tbl.get("id") in seen:
            max_id += 1
            tbl.set("id", str(max_id))
        seen.add(tbl.get("id"))

    agenda = cfg.get("안건명") or cfg.get("agenda") or ""
    lead = cfg.get("대표발의") or cfg.get("lead") or ""
    title = cfg.get("제목") or cfg.get("title") or "발의의원 서명부"

    blocks = [list(sec)[i:i + BLOCK_LEN] for i in range(0, len(sec), BLOCK_LEN)]
    for pi, blk in enumerate(blocks):
        set_para_text(blk[0], 0, title)
        if lead:
            set_para_text(blk[1], 0, "(%s 의원 대표발의)" % lead)
        if agenda:
            set_para_text(blk[3], 1, "안건명: %s" % agenda)

        for ri, tr in enumerate(blk[5].find(".//" + Q("tbl")).findall(Q("tr"))[1:]):
            idx = pi * ROWS_PER_PAGE + ri
            cells = tr.findall(Q("tc"))
            set_cell_text(cells[COL_NO], str(idx + 1))
            org, name = members[idx] if idx < len(members) else ("", "")
            set_cell_text(cells[COL_ORG], org)
            set_cell_text(cells[COL_NAME], name)
            # 서명란(COL_SIGN)은 손서명용이므로 절대 채우지 않는다.
            drop_lsa_row(tr)

    tree.write(sec_path, encoding="UTF-8", xml_declaration=True, standalone=True)

    if os.path.exists(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(work, "mimetype"), "mimetype", zipfile.ZIP_STORED)
        for n in names:
            if n == "mimetype":
                continue
            fp = os.path.join(work, n.replace("/", os.sep))
            if os.path.isfile(fp):
                z.write(fp, n)
    shutil.rmtree(work)
    print("staged: %s (pages=%d, members=%d, 정원=%d)"
          % (out_path, pages, len(members), pages * ROWS_PER_PAGE))
    return out_path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("out")
    ap.add_argument("--template",
                    default=os.path.join(here, "..", "assets", "template.hwpx"))
    a = ap.parse_args()
    with open(a.config, encoding="utf-8") as f:
        build(json.load(f), os.path.abspath(a.template), os.path.abspath(a.out))


if __name__ == "__main__":
    main()
