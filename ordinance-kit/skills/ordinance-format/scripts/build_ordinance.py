# -*- coding: utf-8 -*-
"""조례 표준 서식 HWPX 빌더.

사용법:
  python build_ordinance.py --template <출력.hwpx>   # 빈 서식 템플릿 복사
  python build_ordinance.py <spec.json>              # 조문 내용을 서식으로 조판

spec.json 형식은 SKILL.md 참조. 한글(HWP)이 설치된 Windows에서만 조판 모드 동작.
검증(신명조 등록·문단별 내어쓰기 값)까지 통과해야 최종 파일이 저장된다.
출력 폴더 기본값은 환경변수 ORDINANCE_OUT_DIR(없으면 출력폴더).

2026-10-02: spec에 `compare_table`([[현행, 개정안], …])이 있으면 부칙 뒤 새 쪽에
「신ㆍ구조문대비표」를 붙인다 — ordinance-uian/scripts/compare_table.py(경기도의회 의안
실물 규격)를 신명조로 이식한 뒤 한/글 COM으로 다시 정품 저장한다.
2026-10-12: (감사 261012 반영) 유형 판별 통일(detect_type)·개정문 두문 자동 렌더, 절 레벨,
모르는 레벨/text 누락 오류, 장 맨 앞 빈 줄 중복 제거, 미검증 줄은 들여쓰기 없는 빈 줄,
COM 전 마커 검사(fail fast), verify 문단별 내어쓰기 대조, 출력 폴더 환경변수화.
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
DEFAULT_OUT_DIR = os.environ.get("ORDINANCE_OUT_DIR") or r"출력폴더"
# 대비표 모듈은 ordinance-uian 스킬에 있다(두 서식이 같은 실물 규격을 쓴다)
SKILLS_ROOT = os.environ.get("ORDINANCE_SKILLS_DIR") or os.path.dirname(SKILL_DIR)
COMPARE_DIR = os.path.join(SKILLS_ROOT, "ordinance-uian", "scripts")

ALIGN_JUSTIFY = 0
ALIGN_CENTER = 3
FACE = "신명조"

# 레벨: (앞 공백 칸수, 내어쓰기 값, 음수=내어쓰기)
# 단위 주의: COM ParagraphShape의 Indentation은 pt×200이다(글자 Height는 pt×100과 다름).
# 2026-10-12 실측 — pt×100(-1400)을 넣으면 한/글이 7pt로 그려 「조례만들기(서식)」의
# 첫 줄 내어쓰기(조ㆍ항 14·호 24.4·목 45.4·1) 54.2·가) 75.2·(1) 87.8pt)의 정확히 절반이 됐다.
LEVELS = {
    "조":   (0, -2800),
    "지시": (0, -2800),      # 개정문 지시 줄(두문 포함) — 조 문단처럼, 진하게 없음
    "항":   (2, -2800),
    "호":   (2, -4880),
    "목":   (4, -9080),
    "1)":   (6, -10840),
    "가)":  (8, -15040),
    "(1)":  (10, -17560),
    "(가)": (12, -21760),
}
HEADINGS = ("장", "절")       # 가운데 진하게 제목
ALLOWED = HEADINGS + tuple(LEVELS)

DEFAULT_PROMULGATION = "○○시·도(시·군·구)조례  제        호"
ADDENDA_HEADER = "부      칙"

# ── 유형 판별(공유 규약 — ordinance-suite·ordinance-uian과 같은 규칙, 스킬 독립을 위해 인라인) ──
TYPES = ("제정", "일부개정", "전부개정")


def detect_type(title, explicit=None):
    """제명(공백 전부 제거)에서 유형을 판별한다. 명시 유형이 우선하되, 제명이 개정으로
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
        sys.exit("유형 '%s'은(는) 알 수 없습니다 — 사용 가능: %s" % (explicit, ", ".join(TYPES)))
    if derived != "제정" and derived != ex:
        sys.exit("유형 충돌: 제명 '%s'은(는) %s인데 spec 유형은 %s입니다." % (title, derived, ex))
    return ex


DOUMUN = {"일부개정": "「%s」 일부를 다음과 같이 개정한다.",
          "전부개정": "「%s」 전부를 다음과 같이 개정한다."}
RE_DOUMUN = re.compile(r"다음과\s*같이\s*개정한다")


def current_title(spec):
    cur = (spec.get("현행조례명") or spec.get("current_title") or "").strip().strip("「」").strip()
    if cur:
        return cur
    t = (spec.get("title") or "").strip()
    m = re.match(r"^(.*?)\s*(?:일부\s*개정|전부\s*개정|개정)\s*(조례|규칙)안$", t)
    if not m or not m.group(1).strip():
        sys.exit("개정조례안인데 현행조례명(`현행조례명`/`current_title`)이 없고 제명에서도 추정할 수 없습니다: %r" % t)
    cur = m.group(1).strip()
    if not cur.endswith(m.group(2)):
        cur += " " + m.group(2)
    print("경고: 현행조례명이 없어 제명에서 추정했습니다 → 「%s」 (제출 전 공식 제명과 대조할 것)" % cur)
    return cur


def main_entries(spec):
    """본칙 자리에 찍을 항목. 개정조례안이면 두문 + 개정문(없으면 body). 첫 줄이 이미 두문이면 그대로."""
    kind = detect_type(spec.get("title"), spec.get("유형") or spec.get("type"))
    if kind == "제정":
        return kind, list(spec.get("body", []))
    body = spec.get("개정문") or spec.get("amendment") or spec.get("body") or []
    first = body[0].get("text") if body and isinstance(body[0], dict) else ""
    if RE_DOUMUN.search(first or ""):
        return kind, list(body)
    return kind, [{"level": "지시", "text": DOUMUN[kind] % current_title(spec)}] + list(body)


# ── 미검증 콘텐츠 가드 ──────────────────────────────────────────────
# spec.json 조문 텍스트에 "[원문 미확인 ...]" / "[확인 필요 ...]" 류 마커가 있으면
# 그 조항은 완성본에는 절대 남기지 않고 공란(들여쓰기 없는 빈 줄) 처리한다. 원문은 UNVERIFIED에
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

    def blank():
        if not (rows and rows[-1][0] == "" and rows[-1][2] == 0):   # 빈 줄 중복 방지
            para("")

    para(spec.get("promulgation_line") or DEFAULT_PROMULGATION)
    para("")
    para(spec["title"], align=ALIGN_CENTER, height=1600, bold=1)
    para("")

    def body_para(i, item, where):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            sys.exit(f"{where} {i + 1}번째 항목에 문자열 `text`가 없습니다: {item!r}")
        level = (item.get("level") or "조").strip()
        if level not in ALLOWED:
            sys.exit(f"{where} {i + 1}번째 항목의 레벨 '{level}'은(는) 알 수 없습니다 — "
                     f"사용 가능: {', '.join(ALLOWED)}")
        if level in HEADINGS:                    # 장ㆍ절 제목: 가운데 진하게(앞뒤 빈 줄 1개)
            text = guard(item["text"].strip(), where)
            if text:
                blank(); para(text, align=ALIGN_CENTER, bold=1); para("")
            return
        spaces, indent = LEVELS[level]
        text = guard(item["text"].strip(), where)
        if not text:                             # 미검증·빈 항목 → 들여쓰기 없는 빈 줄
            para("")
            return
        para(" " * spaces + text, indent=indent)

    kind, main = main_entries(spec)
    for i, item in enumerate(main):
        body_para(i, item, "본칙")

    addenda = spec.get("addenda") or []
    if addenda:
        blank()
        para(ADDENDA_HEADER, align=ALIGN_CENTER, bold=1)
        para("")
        for i, item in enumerate(addenda):
            body_para(i, item, "부칙")
    return rows


def assert_clean(rows, pairs=None):
    """COM 실행 전 마커 잔존 검사(fail fast). 대비표 셀은 compare_table의 셀 가드가 처리한다."""
    left = [r[0] for r in rows if UNVERIFIED_RE.search(r[0] or "")]
    if left:
        sys.exit("미검증 마커가 조판 대상에 남았습니다 — guard() 누락: %s" % left[:3])
    for pr in pairs or []:
        for cell in pr:
            if UNVERIFIED_RE.search(str(cell or "")):
                sys.exit("미검증 마커가 대비표에 남았습니다 — compare_table.clean_pairs() 를 거칠 것")


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


def com_resave(src_hwpx, dst_hwpx, dst_pdf=None):
    """재패키징한 HWPX를 한/글 COM으로 열어 정품 재저장(+PDF). Open/SaveAs는 실패해도
    False만 돌려주므로 반환값을 검사한다."""
    import win32com.client as win32

    hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModuleExample")
    try:
        hwp.XHwpWindows.Item(0).Visible = False
    except Exception:
        pass
    try:
        if not hwp.Open(src_hwpx, "HWPX", "forceopen:true"):
            raise RuntimeError("COM Open 실패: %s" % src_hwpx)
        if os.path.exists(dst_hwpx):
            os.remove(dst_hwpx)
        if not (hwp.SaveAs(dst_hwpx, "HWPX", "") and os.path.exists(dst_hwpx)):
            raise RuntimeError("COM SaveAs HWPX 실패")
        if dst_pdf:
            if os.path.exists(dst_pdf):
                os.remove(dst_pdf)
            if not (hwp.SaveAs(dst_pdf, "PDF", "") and os.path.exists(dst_pdf)):
                raise RuntimeError("COM SaveAs PDF 실패")
    finally:
        try:
            hwp.Run("FileQuit")
        except Exception:
            pass


def load_compare_table():
    if not os.path.isdir(COMPARE_DIR):
        raise RuntimeError("ordinance-uian 스킬(compare_table.py)이 없어 대비표를 붙일 수 없습니다: %s"
                           % COMPARE_DIR)
    if COMPARE_DIR not in sys.path:
        sys.path.insert(0, COMPARE_DIR)
    import compare_table
    return compare_table


def inject_compare_table(stage_hwpx, pairs, work, title=None):
    """stage_hwpx(정품) → 대비표 삽입(재패키징) → COM 재저장 경로. (삽입본 경로) 반환."""
    compare_table = load_compare_table()
    injected = os.path.join(work, "stage_compare.hwpx")
    kw = {"font_override": FACE}
    if title:
        kw["title"] = title
    compare_table.append_to_file(stage_hwpx, injected, pairs, section=0, **kw)
    with zipfile.ZipFile(injected) as zf:
        n = compare_table.verify_block(zf.read("Contents/section0.xml"), pairs)
    print(f"신ㆍ구조문대비표 {n}행 삽입")
    return injected


def verify(stage_hwpx, rows):
    """글꼴 등록 + **문단마다** 내어쓰기가 그 줄의 레벨 값과 같은지 + 마커 잔존 0."""
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

    def in_case_branch(elem, stop):
        # paraPr의 margin은 <hp:switch> 안 case/default 양쪽에 사본이 있고
        # case 쪽은 단위 표현이 달라 값이 다를 수 있다 — default 계열만 센다.
        cur = elem.getparent()
        while cur is not None and cur is not stop:
            if ln(cur) == "case":
                return True
            cur = cur.getparent()
        return False

    intent_of = {}
    for p in header.iter():
        if ln(p) != "paraPr":
            continue
        for x in p.iter():
            if ln(x) == "intent" and not in_case_branch(x, p):
                intent_of[p.get("id")] = int(x.get("value"))

    # 구역의 최상위 문단(표 안 문단 제외) — COM 조판은 rows 1개당 문단 1개
    paras = [c for c in section if ln(c) == "p"]
    assert len(paras) >= len(rows), f"문단 수 부족: 생성 {len(paras)}, 기대 ≥{len(rows)}"
    bad = []
    for i, (row, p) in enumerate(zip(rows, paras)):
        text, _, indent, _, _ = row
        got = intent_of.get(p.get("paraPrIDRef"))
        if text and got != indent:              # 빈 줄은 레벨이 없으니 대조하지 않는다
            bad.append(f"#{i} {text[:20]!r}: 내어쓰기 {got} ≠ 기대 {indent}")
        ptxt = "".join(t.text or "" for t in p.iter() if ln(t) == "t").strip()
        if text.strip() and ptxt != text.strip():
            print(f"경고: 문단 #{i} 텍스트가 다릅니다 — 생성 {ptxt[:30]!r} / 기대 {text.strip()[:30]!r}")
    assert not bad, "문단별 내어쓰기 불일치 %d건:\n  %s" % (len(bad), "\n  ".join(bad[:10]))
    whole = "".join(section.itertext())
    assert not UNVERIFIED_RE.search(whole), "미검증 마커가 완성본에 남았습니다 — guard() 누락 지점을 찾아 고칠 것"
    print(f"VERIFY OK — fonts={sorted(faces)}, 문단 {len(rows)}개 내어쓰기 일치")


def main():
    # 파이프로 실행되면 표준출력이 cp949로 잡혀 진단 메시지(—·한글)에서 죽는다
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__ or "usage: build_ordinance.py --template <out.hwpx> | <spec.json>")
        sys.exit(0)
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
    pairs = spec.get("compare_table") or []
    if pairs:
        # 대비표 셀 가드를 COM 전에 돌려 보고를 먼저 확보하고, 정리된 쌍으로 진행한다
        pairs = load_compare_table().clean_pairs(pairs)
    assert_clean(rows, pairs)                       # fail fast — 한/글을 띄우기 전에

    work = tempfile.mkdtemp(prefix="ordinance_")
    stage_hwpx = os.path.join(work, "stage.hwpx")
    want_pdf = bool(spec.get("pdf_preview"))
    stage_pdf = os.path.join(work, "stage.pdf") if want_pdf else None
    try:
        if pairs:
            # 표를 붙인 뒤 다시 정품 저장해야 하므로 PDF는 재저장 단계에서 만든다
            com_build(rows, stage_hwpx, None)
            injected = inject_compare_table(stage_hwpx, pairs, work, spec.get("compare_title"))
            stage_hwpx = os.path.join(work, "stage_final.hwpx")
            com_resave(injected, stage_hwpx, stage_pdf)
        else:
            com_build(rows, stage_hwpx, stage_pdf)
        verify(stage_hwpx, rows)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        shutil.copyfile(stage_hwpx, out)
        print(f"OUTPUT: {out}")
        if want_pdf:
            pdf_out = os.path.splitext(out)[0] + ".pdf"
            shutil.copyfile(stage_pdf, pdf_out)
            print(f"PDF PREVIEW: {pdf_out}")
        notes = UNVERIFIED + (load_compare_table().UNVERIFIED if pairs else [])
        if notes:
            print(f"[미검증 — 완성본에서 공란 처리함, 대화창으로만 보고할 것] {len(notes)}건")
            for note in notes:
                print(f"  - {note}")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
