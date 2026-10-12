# -*- coding: utf-8 -*-
"""스테이징 HWPX를 한/글 COM으로 열어 '정품' HWPX로 재저장한다.

Python이 재패키징한 zip은 한/글 보안수준 '높음'에서 변조 문서로 판정돼 열리지
않는다. COM Open→SaveAs를 거치면 linesegarray·미리보기가 재생성되며 정상 문서가
된다. PDF 경로를 함께 주면 검증용 렌더도 만든다.

usage: python com_save.py stage.hwpx out.hwpx [out.pdf]

안전장치(2026-10-12, 감사 261012 — 사본 4개 ordinance-uian·ordinance-suite·ipbeop-yego·
signature-sheet 는 바이트 동일해야 한다):
  · 한/글은 try/finally 로 반드시 Quit — 실패해도 HWP 프로세스가 남지 않는다.
  · 같은 폴더의 임시 파일에 저장한 뒤 크기>0 을 확인하고 os.replace 로 교체한다.
    저장이 실패해도 이전 정상 산출물은 지워지지 않는다.
  · 실패하면 exit≠0(메시지는 stderr). PDF 저장 실패는 경고(stderr)만 내고 HWPX는 유지한다.
  · stdout 형식(saved: / pdf: / PageCount:)은 그대로 — run_suite 등 호출자가 읽는다.
"""
import os
import sys


def _tmp_path(final):
    d, base = os.path.split(final)
    return os.path.join(d, "~comsave_%d_%s" % (os.getpid(), base))


def _ok_file(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _drop(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def com_save(stage, out, pdf=None):
    stage, out = os.path.abspath(stage), os.path.abspath(out)
    if not os.path.exists(stage):
        raise SystemExit("스테이징 파일 없음: %s" % stage)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pdf = os.path.abspath(pdf) if pdf else None
    tmp = _tmp_path(out)
    tmp_pdf = _tmp_path(pdf) if pdf else None

    import win32com.client as win32

    hwp = None
    pages = None
    pdf_ok = False
    try:
        hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModuleExample")
        # Open()은 실패해도 예외 없이 False만 반환한다 — 검사를 빠뜨리면 빈 문서가
        # 조용히 저장된다.
        if not hwp.Open(stage, "HWPX", "forceopen:true"):
            raise SystemExit("열기 실패: %s" % stage)
        _drop(tmp)
        # SaveAs도 실패하면 False만 — 반환값과 실파일(크기>0)을 모두 본다
        if not hwp.SaveAs(tmp, "HWPX", "") or not _ok_file(tmp):
            raise SystemExit("저장 실패: %s (이전 산출물은 그대로 둠)" % out)
        if pdf:
            _drop(tmp_pdf)
            try:
                pdf_ok = bool(hwp.SaveAs(tmp_pdf, "PDF", "")) and _ok_file(tmp_pdf)
            except Exception as e:                      # PDF는 검증용 — HWPX를 살린다
                sys.stderr.write("경고: PDF 저장 중 예외: %s\n" % e)
                pdf_ok = False
        try:
            pages = hwp.PageCount
        except Exception:
            pages = None
    except SystemExit:
        _drop(tmp)
        _drop(tmp_pdf)
        raise
    except Exception as e:
        _drop(tmp)
        _drop(tmp_pdf)
        raise SystemExit("한/글 COM 오류: %s" % e)
    finally:
        if hwp is not None:
            try:
                hwp.Clear(1)
            except Exception:
                pass
            try:
                hwp.Quit()
            except Exception:
                pass

    # 한/글이 파일을 놓은 뒤 교체한다(최종 경로를 한/글로 열어 두었으면 여기서 실패)
    try:
        os.replace(tmp, out)
    except OSError as e:
        raise SystemExit("최종 경로로 교체 실패(%s): %s — 새 저장본은 %s 에 남겨 둠" % (e, out, tmp))
    if not _ok_file(out):
        raise SystemExit("저장 확인 실패(파일 없음 또는 0바이트): %s" % out)
    print("saved: %s" % out)

    if pdf:
        if pdf_ok:
            try:
                os.replace(tmp_pdf, pdf)
                print("pdf: %s" % pdf)
            except OSError as e:
                sys.stderr.write("경고: PDF 교체 실패(%s) — %s 에 남겨 둠\n" % (e, tmp_pdf))
        else:
            _drop(tmp_pdf)
            _drop(pdf)          # 이전 렌더가 남아 있으면 새 HWPX의 검증본으로 오인된다
            sys.stderr.write("경고: PDF 저장 실패 — %s 를 만들지 못했습니다(HWPX는 정상 저장)\n" % pdf)

    print("PageCount: %s" % pages)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
        print("usage: python com_save.py stage.hwpx out.hwpx [out.pdf]")
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help") else 2)
    com_save(*sys.argv[1:4])
