# -*- coding: utf-8 -*-
"""조례 표준 서식 HWPX 빌더.

사용법:
  python build_ordinance.py --template <출력.hwpx>   # 빈 서식 템플릿 복사
  python build_ordinance.py <spec.json>              # 조문 내용을 서식으로 조판

spec.json 형식은 SKILL.md 참조. 한글(HWP)이 설치된 Windows에서만 조판 모드 동작.
검증(신명조 등록·내어쓰기 값)까지 통과해야 최종 파일이 저장된다.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import zipfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "assets", "ordinance-template.hwpx")
DEFAULT_OUT_DIR = os.environ.get("ORDINANCE_OUT_DIR") or os.getcwd()

ALIGN_JUSTIFY = 0
ALIGN_CENTER = 3
FACE = "신명조"

# 레벨: (앞 공백 칸수, 내어쓰기 hwpunit(pt*100), 음수=내어쓰기)
LEVELS = {
    "조":   (0, -1400),
    "항":   (2, -1400),
    "호":   (2, -2440),
    "목":   (4, -4540),
    "1)":   (6, -5420),
    "가)":  (8, -7520),
    "(1)":  (10, -8780),
    "(가)": (12, -10880),
}

DEFAULT_PROMULGATION = "○○시·도(시·군·구)조례  제        호"
ADDENDA_HEADER = "부      칙"

# ── 미검증 콘텐츠 가드 ──────────────────────────────────────────────
# spec.json 조문 텍스트에 "[원문 미확인 ...]" / "[확인 필요 ...]" 류 마커가 있으면
# 그 조항은 완성본에는 절대 남기지 않고 공란(빈 줄) 처리한다. 원문은 UNVERIFIED에
# 적재해 빌드 끝에 stdout으로만 보고한다(대화창 전달용, 문서에는 쓰지 않는다).
UNVERIFIED_RE = re.compile(r"\[\s*(?:원문\s*미확인|미확인|확인\s*필요|검증\s*필요|미검증)[^\]]*\]")
UNVERIFIED = []


def guard(text, where):
    if text and UNVERIFIED_RE.search(text):
        UNVERIFIED.append("%s: %s" % (where, text.strip()))
        return ""
    return text


def copy_template(out_path):
    if not os.path.exists(TEMPLATE):
        sys.exit(f"템플릿 없음: {TEMPLATE}")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    shutil.copyfile(TEMPLATE, out_path)
    print(f"TEMPLATE COPIED: {out_path}")


def build_rows(spec):
    """spec -> [(text, align, indent, height_pt100, bold)] 문단 목록."""
    rows = []

    def para(text, align=ALIGN_JUSTIFY, indent=0, height=1400, bold=0):
        rows.append((text, align, indent, height, bold))

    para(spec.get("promulgation_line") or DEFAULT_PROMULGATION)
    para("")
    para(spec["title"], align=ALIGN_CENTER, height=1600, bold=1)
    para("")

    def body_para(item, where):
        level = item.get("level", "조")
        if level not in LEVELS:
            sys.exit(f"알 수 없는 레벨 '{level}' — 사용 가능: {list(LEVELS)}")
        spaces, indent = LEVELS[level]
        text = guard(item["text"].lstrip(), where)
        para(" " * spaces + text, indent=indent)

    for item in spec.get("body", []):
        body_para(item, "본칙")

    addenda = spec.get("addenda") or []
    if addenda:
        para("")
        para(ADDENDA_HEADER, align=ALIGN_CENTER, bold=1)
        para("")
        for item in addenda:
            body_para(item, "부칙")
    return rows


def com_build(rows, stage_hwpx, stage_pdf):
    import win32com.client as win32

    hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModuleExample")
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:
        pass

    def set_charshape(height, bold):
        act = hwp.CreateAction("CharShape")
        pset = act.CreateSet()
        act.GetDefault(pset)
        for lang in ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol", "User"):
            pset.SetItem("FaceName" + lang, FACE)
            pset.SetItem("FontType" + lang, 1)  # 1=TTF — 없으면 FaceName이 조용히 무시됨
        pset.SetItem("Height", height)
        pset.SetItem("Bold", bold)
        act.Execute(pset)

    def set_parashape(align, indent):
        act = hwp.CreateAction("ParagraphShape")
        pset = act.CreateSet()
        act.GetDefault(pset)
        pset.SetItem("AlignType", align)
        pset.SetItem("Indentation", indent)
        pset.SetItem("LeftMargin", 0)
        act.Execute(pset)

    def insert_text(text):
        act = hwp.CreateAction("InsertText")
        pset = act.CreateSet()
        act.GetDefault(pset)
        pset.SetItem("Text", text)
        act.Execute(pset)

    try:
        for i, (text, align, indent, height, bold) in enumerate(rows):
            set_parashape(align, indent)
            set_charshape(height, bold)
            if text:
                insert_text(text)
            if i < len(rows) - 1:
                hwp.Run("BreakPara")
        ok1 = hwp.SaveAs(stage_hwpx, "HWPX", "")
        ok2 = hwp.SaveAs(stage_pdf, "PDF", "") if stage_pdf else True
        # SaveAs는 실패해도 예외 없이 False — 반환값·실파일 반드시 검사
        if not (ok1 and os.path.exists(stage_hwpx)):
            raise RuntimeError("SaveAs HWPX 실패")
        if stage_pdf and not (ok2 and os.path.exists(stage_pdf)):
            raise RuntimeError("SaveAs PDF 실패")
    finally:
        try:
            hwp.Run("FileQuit")
        except Exception:
            pass


def verify(stage_hwpx, rows):
    from lxml import etree

    def ln(e):
        return etree.QName(e.tag).localname

    with zipfile.ZipFile(stage_hwpx) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype", "mimetype이 첫 엔트리가 아님"
        header_name = next(n for n in names if "header" in n and n.endswith(".xml"))
        section_name = next(n for n in names if "section" in n and n.endswith(".xml"))
        header = etree.fromstring(zf.read(header_name))
        section = etree.fromstring(zf.read(section_name))

    faces = {f.get("face") for f in header.iter() if ln(f) == "font"}
    assert FACE in faces, f"{FACE} 글꼴이 등록되지 않음 (등록: {faces})"

    intents = set()
    for x in section.iter():
        if ln(x) == "p" and x.get("paraPrIDRef"):
            intents.add(x.get("paraPrIDRef"))
    def in_case_branch(elem, stop):
        # paraPr의 margin은 <hp:switch> 안 case/default 양쪽에 사본이 있고
        # case 쪽은 단위 표현이 달라 값이 다를 수 있다 — default 계열만 센다.
        cur = elem.getparent()
        while cur is not None and cur is not stop:
            if ln(cur) == "case":
                return True
            cur = cur.getparent()
        return False

    parapr_intents = set()
    for p in header.iter():
        if ln(p) != "paraPr" or p.get("id") not in intents:
            continue
        for x in p.iter():
            if ln(x) == "intent" and not in_case_branch(x, p):
                parapr_intents.add(int(x.get("value")))
    expected = {indent for (_, _, indent, _, _) in rows}
    missing = expected - parapr_intents
    assert not missing, f"내어쓰기 값 누락: {sorted(missing)}"
    whole = "".join(section.itertext())
    assert not UNVERIFIED_RE.search(whole), "미검증 마커가 완성본에 남았습니다 — guard() 누락 지점을 찾아 고칠 것"
    print(f"VERIFY OK — fonts={sorted(faces)}, intents={sorted(parapr_intents)}")


def main():
    # 파이프로 실행되면 표준출력이 cp949로 잡혀 진단 메시지(—·한글)에서 죽는다
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) >= 3 and sys.argv[1] == "--template":
        copy_template(sys.argv[2])
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    with open(sys.argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    if "title" not in spec:
        sys.exit("spec에 title이 필요합니다.")
    out = spec.get("output") or os.path.join(DEFAULT_OUT_DIR, spec["title"] + ".hwpx")
    rows = build_rows(spec)

    work = tempfile.mkdtemp(prefix="ordinance_")
    stage_hwpx = os.path.join(work, "stage.hwpx")
    want_pdf = bool(spec.get("pdf_preview"))
    stage_pdf = os.path.join(work, "stage.pdf") if want_pdf else None
    try:
        com_build(rows, stage_hwpx, stage_pdf)
        verify(stage_hwpx, rows)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        shutil.copyfile(stage_hwpx, out)
        print(f"OUTPUT: {out}")
        if want_pdf:
            pdf_out = os.path.splitext(out)[0] + ".pdf"
            shutil.copyfile(stage_pdf, pdf_out)
            print(f"PDF PREVIEW: {pdf_out}")
        if UNVERIFIED:
            print(f"[미검증 — 완성본에서 공란 처리함, 대화창으로만 보고할 것] {len(UNVERIFIED)}건")
            for note in UNVERIFIED:
                print(f"  - {note}")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
