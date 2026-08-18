# -*- coding: utf-8 -*-
"""스테이징 HWPX를 한/글 COM으로 열어 '정품' HWPX로 재저장한다.

Python이 재패키징한 zip은 한/글 보안수준 '높음'에서 변조 문서로 판정돼 열리지
않는다. COM Open→SaveAs를 거치면 linesegarray·미리보기가 재생성되며 정상 문서가
된다. PDF 경로를 함께 주면 검증용 렌더도 만든다.

usage: python com_save.py stage.hwpx out.hwpx [out.pdf]
"""
import os
import sys

import win32com.client as win32


def com_save(stage, out, pdf=None):
    stage, out = os.path.abspath(stage), os.path.abspath(out)
    if not os.path.exists(stage):
        raise SystemExit("스테이징 파일 없음: %s" % stage)

    hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModuleExample")
    # Open()은 실패해도 예외 없이 False만 반환한다 — 검사를 빠뜨리면 빈 문서가
    # 조용히 저장된다.
    if not hwp.Open(stage, "HWPX", "forceopen:true"):
        hwp.Quit()
        raise SystemExit("열기 실패: %s" % stage)

    if os.path.exists(out):
        os.remove(out)
    if not hwp.SaveAs(out, "HWPX", ""):
        hwp.Quit()
        raise SystemExit("저장 실패: %s" % out)
    print("saved: %s" % out)

    if pdf:
        pdf = os.path.abspath(pdf)
        if os.path.exists(pdf):
            os.remove(pdf)
        if hwp.SaveAs(pdf, "PDF", ""):
            print("pdf: %s" % pdf)

    print("PageCount: %s" % hwp.PageCount)
    hwp.Clear(1)
    hwp.Quit()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    com_save(*sys.argv[1:4])
