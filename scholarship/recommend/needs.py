# -*- coding: utf-8 -*-
"""[RECOMMEND] 5-A: '확인 필요' 판정 항목이 학생의 어떤 입력 항목(항목 사전 코드)으로 풀릴 수 있는지 표시한다.

Check.needs 는 '후보'다. 이미 답한 항목은 질문을 만들 때(followup.py) 걸러낸다. 그래서 값이 있는데도 확인 필요인 경우(만점이 달라 비교할 수 없는 경우 등)는 질문이 되지 않는다.
어떤 입력으로도 풀리지 않는 조건(공고의 문장 조건, 소득 중위소득·재산 기준, 결합 방식이 모호한 조건)은 needs 가 비어 있고 '공고문 확인' 목록으로 따로 보여 준다.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

UNKNOWN = "UNKNOWN"

# 판정 항목(Check.key) -> 풀어 줄 수 있는 입력 항목 후보 (gpa·region 은 아래에서 따로 정한다)
KEY_NEEDS: Dict[str, Tuple[str, ...]] = {
    "levels": ("GRAD",), "grade": ("YEAR",), "enrollment": ("LV",), "credits": ("CR",), "terms": ("SEM",), "income": ("INC",), "nationality": ("NAT",),
    "major_group": ("MAJG",), "special": ("SPECIAL",), "age": ("AGE",), "gender": ("GEN",), "entry": ("ENT",), "transfer": ("TRF",),
    "new_student": ("SEM", "TRF"), "language": ("LANG",),
}
# 풀 수 있는 입력이 없는 판정 항목: other(공고 문장 조건) / exclusion / pending(소득 확장) / major(학과 문구) / school(서버 설정)
PLACE_CODE = {"RESIDENCE_SELF": "RES", "RESIDENCE_PARENT": "RESP", "GRADUATED_SCHOOL": "HSL"}


def gpa_needs(g: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    """성적 조건의 기준(직전학기/누적/직전 N개 학기)과 만점(100점제 여부)에 따라 필요한 입력 항목."""
    if not g:
        return ("GL", "GC")
    scales = [g.get("scale")] + [a.get("scale") for a in g.get("alternatives") or [] if isinstance(a, dict)]
    scales = [s for s in scales if s is not None]
    has_pct = any(s == 100 for s in scales)
    only_pct = bool(scales) and all(s == 100 for s in scales)
    basis = g.get("basis") or "UNKNOWN"
    if basis == "ENTRANCE":
        return ()
    if basis == "LAST_N":
        return ("GN",)
    last = ("SP",) if only_pct else (("GL",) + (("SP",) if has_pct else ()))
    cum = ("GCP",) if only_pct else (("GC",) + (("GCP",) if has_pct else ()))
    if basis == "LAST_SEMESTER":
        return last
    if basis == "CUMULATIVE":
        return cum
    return last + cum


def needs_for(check: Any, d: Optional[Dict[str, Any]] = None) -> Tuple[str, ...]:
    if check.key == "gpa":
        return gpa_needs((d or {}).get("gpa"))
    if check.key == "region":                                              # v2 지역 조건: 본인 주민등록 (학교 소재지 조건은 서버 설정이라 질문하지 않는다)
        return () if "학교" in (check.message or "") else ("RES",)
    return KEY_NEEDS.get(check.key, ())


def annotate(checks: Iterable[Any], d: Optional[Dict[str, Any]] = None) -> None:
    """확인 필요 항목 중 아직 표시하지 않은(needs 가 None) 것에 기본 후보를 채운다. 이미 정밀하게 정해진 것(빈 후보 포함)은 건드리지 않는다. d: 그 조건이 들어 있는 공고 또는 유형."""
    for c in checks:
        if c.status == UNKNOWN and c.needs is None:                         # ()는 '입력으로 풀 수 없다'고 이미 정한 것이라 덮어쓰지 않는다
            c.needs = needs_for(c, d)


def merge(*groups: Iterable[str]) -> Tuple[str, ...]:
    out = []
    for g in groups:
        for x in g:
            if x not in out:
                out.append(x)
    return tuple(out)
