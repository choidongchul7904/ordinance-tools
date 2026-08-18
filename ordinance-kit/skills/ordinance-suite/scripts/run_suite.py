# -*- coding: utf-8 -*-
"""조례안 문서 4종 일괄 생성 오케스트레이터.

통합 스펙(suite.json) 하나로 아래 4개 HWPX를 만든다. 각 서식은 규격이 서로 다르며
(의안 규격 ≠ 표준 입안 규격) 담당 스킬의 빌더를 그대로 호출한다 — 규격을 섞지 않기
위해서다.

  1. 조례안 완성본(의안)   ordinance-uian/scripts/build_uian.py
  2. 조례안 표준 입안서식  ordinance-format/scripts/build_ordinance.py
  3. 입법예고 의뢰서       ipbeop-yego/scripts/build_yego.py
  4. 발의의원 서명부       signature-sheet/scripts/build_sheet.py

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

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.environ.get("ORDINANCE_SKILLS_DIR") or os.path.dirname(os.path.dirname(HERE))
PY = sys.executable
COM_SAVE = os.path.join(HERE, "com_save.py")

JOBS = ["완성본", "표준서식", "입법예고", "서명부"]

# 저장 폴더: 환경변수 ORDINANCE_OUT_DIR, 없으면 현재 폴더 (PC마다 경로가 다르므로)
DEFAULT_OUT = os.environ.get("ORDINANCE_OUT_DIR") or os.getcwd()


def sh(args, cwd=None):
    # 자식 프로세스의 표준출력이 cp949로 잡히면 한글·—에서 죽는다
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run([str(x) for x in args], cwd=cwd, capture_output=True, env=env)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    return r.returncode, out.strip()


def need(path, what):
    if not os.path.exists(path):
        raise RuntimeError("%s 스킬이 없습니다: %s" % (what, path))
    return path


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
        "body": spec["본칙"],
        "addenda": spec.get("부칙") or [],
        "pdf_preview": bool(pdf),
    }
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
    kind = "개정" if "개정" in spec.get("유형", "제정") else "제정"
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
    ap.add_argument("--stamp", default=dt.date.today().strftime("%y%m%d"))
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    os.makedirs(a.outdir, exist_ok=True)
    names = filenames(spec, a.stamp)
    todo = [j for j in JOBS if j in [x.strip() for x in a.only.split(",")]]

    work = tempfile.mkdtemp(prefix="ordsuite_")
    results = []
    for job in todo:
        out = os.path.join(a.outdir, names[job])
        pdf = os.path.join(work, job + ".pdf") if a.pdf else None
        print("\n───── %s → %s" % (job, names[job]), flush=True)
        try:
            log = RUNNERS[job](spec, work, out, pdf)
            print(log)
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
    if spec.get("_미확정"):
        print(" 미확정(사용자 확인 필요): %s" % ", ".join(spec["_미확정"]))
    sys.exit(1 if any(r[1] != "OK" for r in results) else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
