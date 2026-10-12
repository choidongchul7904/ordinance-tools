# -*- coding: utf-8 -*-
"""조례안 문서 4종 일괄 생성 오케스트레이터.

통합 스펙(suite.json) 하나로 아래 4개 HWPX를 만든다. 각 서식은 규격이 서로 다르며
(의안 규격 ≠ 표준 입안 규격) 담당 스킬의 빌더를 그대로 호출한다 — 규격을 섞지 않기
위해서다.

  1. 조례안 완성본(의안)   ordinance-uian/scripts/build_uian.py
  2. 조례안 표준 입안서식  ordinance-format/scripts/build_ordinance.py
  3. 입법예고 의뢰서       ipbeop-yego/scripts/build_yego.py
  4. 발의의원 서명부       signature-sheet/scripts/build_sheet.py

개정조례(유형 일부개정·전부개정)면 spec `대비표`로 1·2·3 모두에 신ㆍ구조문대비표가 들어간다
(2026-10-02). 완성본·표준서식은 ordinance-uian/scripts/compare_table.py(의안 실물 규격).

한 서식이 실패해도 나머지는 계속 만들고, 마지막에 성공/실패를 표로 보고한다.

usage:
    python run_suite.py suite.json --outdir "출력폴더"
    python run_suite.py suite.json --only 완성본,입법예고 --pdf
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ordtype import TypeConflict, detect_type, is_amend  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.environ.get("ORDINANCE_SKILLS_DIR") or os.path.dirname(os.path.dirname(HERE))
PY = sys.executable
COM_SAVE = os.path.join(HERE, "com_save.py")

JOBS = ["완성본", "표준서식", "입법예고", "서명부"]

# `_미확정` 은 스펙 키 목록이다. 사람에게 보일 때만 설명을 붙인다.
LABEL = {"관계법령": "관계법령 발췌(입법예고 참고자료)",
         "공동발의": "공동발의 의원 명단(서명부)",
         "소관위원회": "소관위원회(입법예고 예고문)",
         "문의처": "문의처(입법예고)"}

# 다른 PC에서도 동작하도록 — 지정 폴더가 없으면 현재 폴더에 저장한다
DEFAULT_OUT = os.environ.get("ORDINANCE_OUT_DIR") or os.getcwd()


def sh(args, cwd=None):
    # 자식 프로세스의 표준출력이 cp949로 잡히면 한글·—에서 죽는다
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, env=env)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out.strip()


# ── G3 센티널 게이트 ────────────────────────────────────────────────────────
# 미완성 자리는 `[[TODO:…]]` 로 표시된다(fetch_current.py). 경고만 하면 언젠가
# 무시되고 제출본에 그대로 나간다. 그래서 **해당 서식의 빌드를 실패시킨다**.
TODO_MARK = "[[TODO:"
# 서식별로 실제 사용하는 필드만 검사한다 — 쓰지도 않는 필드 때문에 막지 않기 위해서다
TODO_FIELDS = {
    "완성본": ("본칙", "부칙", "제안이유", "주요내용", "참고사항", "대비표"),
    "표준서식": ("본칙", "부칙", "대비표"),
    "입법예고": ("본칙", "부칙", "개정문", "대비표", "현행조례", "관계법령",
                 "제안이유", "주요내용"),
    "서명부": ("공동발의",),
}


def scan_todo(obj, path=""):
    """spec 안의 [[TODO:…]] 위치를 전부 찾는다."""
    hits = []
    if isinstance(obj, str):
        if TODO_MARK in obj:
            hits.append((path, " ".join(obj.split())[:70]))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            hits += scan_todo(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits += scan_todo(v, "%s[%d]" % (path, i))
    return hits


def todo_gate(spec, job, allow):
    if allow:
        return
    hits = []
    for f in TODO_FIELDS.get(job, ()):
        if f in spec:
            hits += scan_todo(spec[f], f)
    if hits:
        lines = ["미완성 표시 %d건 — 채운 뒤 다시 실행하십시오"
                 " (초안 회람용이면 --allow-todo)." % len(hits)]
        lines += ["    %s  %s" % (p, t) for p, t in hits[:6]]
        if len(hits) > 6:
            lines.append("    … 외 %d건" % (len(hits) - 6))
        raise RuntimeError("\n".join(lines))


def need(path, what):
    if not os.path.exists(path):
        raise RuntimeError("%s 스킬이 없습니다: %s" % (what, path))
    return path


def unique_path(path):
    """같은 날 다시 실행해도 기존 산출물을 조용히 덮어쓰지 않는다 — _2, _3 …"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    k = 2
    while os.path.exists("%s_%d%s" % (base, k, ext)):
        k += 1
    return "%s_%d%s" % (base, k, ext)


# 서식별로 실제 찍히는 사용자 확인 필드 — 비었거나 ○ 자리표시자면 WARN
PLACEHOLDER_FIELDS = {"입법예고": ("소관위원회", "문의처", "대표발의"),
                      "완성본": ("대표발의",),
                      "서명부": ("대표발의",)}


def is_placeholder(v):
    if v in (None, "", [], {}):
        return True
    return isinstance(v, str) and (not v.strip() or "○" in v or "    " in v)


def placeholder_warnings(spec, job):
    return [k for k in PLACEHOLDER_FIELDS.get(job, ()) if is_placeholder(spec.get(k))]


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "", name).strip()


def com_save(stage, out, pdf=None):
    args = [PY, COM_SAVE, stage, out] + ([pdf] if pdf else [])
    code, log = sh(args)
    if code != 0 or not os.path.exists(out):
        raise RuntimeError("한/글 정품 저장 실패\n%s" % log)
    return log


# ---------------------------------------------------------------- 서식별 작업
def job_uian(spec, work, out, pdf):
    b = need(os.path.join(SKILLS, "ordinance-uian", "scripts", "build_uian.py"), "ordinance-uian")
    sp = os.path.join(work, "uian.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    stage = os.path.join(work, "uian_stage.hwpx")
    code, log = sh([PY, b, sp, stage])
    if code != 0:
        raise RuntimeError(log)
    return log + "\n" + com_save(stage, out, pdf)


def job_format(spec, work, out, pdf):
    b = need(os.path.join(SKILLS, "ordinance-format", "scripts", "build_ordinance.py"),
             "ordinance-format")
    sub = {
        "output": out,
        "title": spec["조례명"],
        "promulgation_line": "%s조례  제        호" % spec.get("공포기관", "경기도"),
        "body": spec.get("본칙") or spec.get("개정문") or [],
        "addenda": spec.get("부칙") or [],
        "pdf_preview": bool(pdf),
        "유형": spec.get("유형", "제정"),
    }
    # 개정조례안: 두문은 빌더가 현행조례명+유형으로 만든다(공유 계약) — 개정문·현행조례명 전달
    if is_amend(spec.get("유형", "")):
        if spec.get("현행조례명"):
            sub["현행조례명"] = spec["현행조례명"]
        if spec.get("개정문"):
            sub["개정문"] = spec["개정문"]
    # 개정조례안이면 표준서식에도 신ㆍ구조문대비표를 붙인다(완성본과 같은 실물 규격)
    if is_amend(spec.get("유형", "")) and spec.get("대비표"):
        sub["compare_table"] = spec["대비표"]
    sp = os.path.join(work, "format.json")
    json.dump(sub, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    code, log = sh([PY, b, sp])
    if code != 0 or not os.path.exists(out):
        raise RuntimeError(log)
    side = os.path.splitext(out)[0] + ".pdf"     # 이 빌더는 PDF를 출력 폴더에 남긴다
    if pdf and os.path.exists(side):
        os.replace(side, pdf)                    # 검증용은 작업폴더로 옮겨 출력 폴더를 깨끗이
    return log


def job_yego(spec, work, out, pdf):
    b = need(os.path.join(SKILLS, "ipbeop-yego", "scripts", "build_yego.py"), "ipbeop-yego")
    kind = "개정" if is_amend(spec.get("유형", "제정")) else "제정"
    if kind == "개정" and not (spec.get("현행조례") and spec.get("대비표")):
        raise RuntimeError("개정조례 입법예고는 현행 조례 전문과 신·구조문 대비표가 "
                           "필요합니다(law-search로 현행 조례를 조회해 spec에 넣으세요).")
    sp = os.path.join(work, "yego.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    stage = os.path.join(work, "yego_stage.hwpx")
    code, log = sh([PY, b, sp, stage, "--type", kind])
    if code != 0:
        raise RuntimeError(log)
    return log + "\n" + com_save(stage, out, pdf)


def job_sign(spec, work, out, pdf):
    b = need(os.path.join(SKILLS, "signature-sheet", "scripts", "build_sheet.py"),
             "signature-sheet")
    cfg = {"안건명": spec["조례명"], "대표발의": spec.get("대표발의", ""),
           "members": spec.get("공동발의") or []}
    if spec.get("서명부쪽수"):
        cfg["pages"] = spec["서명부쪽수"]
    sp = os.path.join(work, "sign.json")
    json.dump(cfg, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    stage = os.path.join(work, "sign_stage.hwpx")
    code, log = sh([PY, b, sp, stage])
    if code != 0:
        raise RuntimeError(log)
    return log + "\n" + com_save(stage, out, pdf)


RUNNERS = {"완성본": job_uian, "표준서식": job_format,
           "입법예고": job_yego, "서명부": job_sign}


def filenames(spec, stamp):
    t = safe(spec["조례명"])
    return {
        "완성본": "%s(완성본)_%s.hwpx" % (t, stamp),
        "표준서식": "%s(표준서식)_%s.hwpx" % (t, stamp),
        "입법예고": "입법예고 의뢰서_%s_%s.hwpx" % (t, stamp),
        "서명부": "발의의원 서명부_%s_%s.hwpx" % (t, stamp),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--outdir", default=DEFAULT_OUT)
    ap.add_argument("--only", default=",".join(JOBS))
    ap.add_argument("--pdf", action="store_true", help="검증용 PDF도 함께 생성")
    ap.add_argument("--allow-todo", action="store_true",
                    help="[[TODO:…]] 가 남아 있어도 빌드한다(초안 회람용)")
    ap.add_argument("--stamp", default=dt.date.today().strftime("%y%m%d"))
    ap.add_argument("--overwrite", action="store_true",
                    help="같은 이름의 기존 산출물을 덮어쓴다(기본: _2, _3 … 을 붙여 새로 저장)")
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    # 유형은 모든 빌더가 같은 규칙으로 — 제명과 명시 유형이 충돌하면 아무것도 만들지 않는다
    try:
        spec["유형"] = detect_type(spec.get("조례명", ""), spec.get("유형"))
    except TypeConflict as e:
        print("중단: %s" % e)
        sys.exit(2)
    want = [x.strip() for x in a.only.split(",") if x.strip()]
    bad = [x for x in want if x not in JOBS]
    if bad:
        print("중단: --only 에 알 수 없는 서식 이름 %s — 쓸 수 있는 이름: %s"
              % (", ".join(bad), ", ".join(JOBS)))
        sys.exit(2)
    todo = [j for j in JOBS if j in want]
    if not todo:
        print("중단: 만들 서식이 없습니다(--only 를 확인하십시오).")
        sys.exit(2)
    os.makedirs(a.outdir, exist_ok=True)
    names = filenames(spec, a.stamp)

    work = tempfile.mkdtemp(prefix="ordsuite_")
    results = []
    for job in todo:
        out = os.path.join(a.outdir, names[job])
        if not a.overwrite:
            out = unique_path(out)
        pdf = os.path.join(work, job + ".pdf") if a.pdf else None
        print("\n───── %s → %s" % (job, os.path.basename(out)), flush=True)
        try:
            todo_gate(spec, job, a.allow_todo)
            log = RUNNERS[job](spec, work, out, pdf)
            print(log)
            w = placeholder_warnings(spec, job)
            if w:
                print("!! 자리표시자 그대로 찍힘: %s — 제출 전 채우십시오" % ", ".join(w))
                results.append((job, "WARN", out + "  (자리표시자: %s)" % ", ".join(w)))
            else:
                results.append((job, "OK", out))
            if pdf and os.path.exists(pdf):
                keep = os.path.join(work, "verify_%s.pdf" % job)
                os.replace(pdf, keep)
                print("verify pdf: %s" % keep)
        except Exception as e:
            print("실패: %s" % e)
            results.append((job, "실패", str(e).splitlines()[-1][:120]))

    print("\n════════ 결과 ════════")
    for job, st, info in results:
        print(" %-6s %-4s %s" % (job, st, info))
    print(" 작업폴더: %s" % work)
    if a.allow_todo and scan_todo(spec):
        print(" !! --allow-todo 로 미완성 표시를 무시하고 빌드했습니다."
              " 제출 전 [[TODO:…]] 를 반드시 채우십시오.")
    # `_미확정` 은 파서가 만든 목록이라 사람이 채운 뒤에도 그대로 남는다.
    # 실제 값이 비어 있는 항목만 골라 보고한다(다 채웠는데 미확정이라 하면 오보고다).
    def unfilled(k):
        return is_placeholder(spec.get(k))

    left = [LABEL.get(k, k) for k in spec.get("_미확정", []) if unfilled(k)]
    if left:
        print(" 미확정(사용자 확인 필요): %s" % ", ".join(left))
    elif spec.get("_미확정"):
        print(" 미확정 항목 없음 (파서가 표시한 %d건 모두 채워짐)" % len(spec["_미확정"]))
    # 실패가 하나라도 있으면 exit 1. WARN(자리표시자 잔존)은 산출물은 만들어졌으므로 0.
    sys.exit(1 if any(r[1] not in ("OK", "WARN") for r in results) else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
