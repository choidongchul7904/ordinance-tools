# -*- coding: utf-8 -*-
"""조례 유형(제정·일부개정·전부개정) 판별 — ordinance-suite 공용 규칙.

모든 빌더가 **같은 규칙**으로 유형을 정해야 한다. 그렇지 않으면 "일부 개정조례안"
(띄어씀) 같은 제명에서 완성본은 개정, 입법예고는 제정 서식으로 갈라진다
(2026-10-12 감사 지적). 규칙(공유 계약):

  1. 제명의 공백을 모두 지운 뒤
     `전부개정` 포함 → 전부개정
     `일부개정` 포함, 또는 `개정조례안`·`개정규칙안`으로 끝남 → 일부개정
     그 밖 → 제정
  2. spec 의 명시 `유형` 이 있으면 그것이 이긴다.
     단, 제명에서 나온 유형이 제정이 아닌데 명시 유형과 다르면 **충돌 — 중단**한다.

ipbeop-yego/scripts/build_yego.py 에 동일한 사본이 인라인돼 있다(스킬 단독 동작).
한쪽을 고치면 다른 쪽도 같이 고칠 것.
"""
import re

TYPES = ("제정", "일부개정", "전부개정")


class TypeConflict(ValueError):
    pass


def type_from_title(title):
    t = re.sub(r"\s+", "", title or "")
    if "전부개정" in t:
        return "전부개정"
    if "일부개정" in t or t.endswith(("개정조례안", "개정규칙안")):
        return "일부개정"
    return "제정"


def detect_type(title, explicit=None):
    """제명(+명시 유형) → 유형. 충돌이면 TypeConflict."""
    derived = type_from_title(title)
    if explicit in (None, ""):
        return derived
    exp = re.sub(r"\s+", "", str(explicit))
    if exp not in TYPES:
        raise TypeConflict("알 수 없는 유형 %r — %s 중 하나여야 합니다" % (explicit, "·".join(TYPES)))
    if derived != "제정" and derived != exp:
        raise TypeConflict("유형 충돌: 제명 「%s」은 %s인데 spec 유형은 %s입니다 — 제명 또는 유형을 고치십시오"
                           % (title, derived, exp))
    return exp


def is_amend(t):
    return t in ("일부개정", "전부개정")
