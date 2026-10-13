# -*- coding: utf-8 -*-
"""[RECOMMEND] 같은 장학의 원 공고와 연장·재공고를 하나로 묶는다 (가장 최근 공고를 대표로).

키 = (재단명, 장학명) 정규화. 연도·학기 같은 숫자는 남겨서 '2026년' 과 '2027년' 은 다른 장학으로 본다.
공고별 이름이 달라 묶이지 않는 경우(이름이 완전히 다름)는 놓칠 수 있고, 반대로 이름이 같은 다른 공고를 묶을 위험이 있으므로
묶인 결과는 항상 merged_from 으로 드러낸다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Tuple

_BOILERPLATE = re.compile(r"선발계획|선발|안내|공고|모집|신청|추가|재공고|기간연장|연장|계획|\([^)]*연장[^)]*\)")
_FOUNDATION_NOISE = re.compile(r"재단법인|사단법인|\(재\)|\(사\)|\(주\)|주식회사")
_SUFFIX = re.compile(r"장학생|장학금|장학")


def _norm(text: str) -> str:
    t = re.sub(r"[\s\(\)\[\]·,\-_/·]", "", text or "").lower()
    return t


def group_key(c: Dict[str, Any]) -> Tuple[str, str]:
    elig = c.get("elig") or {}
    name = elig.get("scholarship_name") or c.get("title") or ""
    foundation = _FOUNDATION_NOISE.sub("", elig.get("foundation") or "")
    n = _norm(_SUFFIX.sub("", _BOILERPLATE.sub("", name)))
    if not n:
        n = _norm(c.get("title") or "")
    return _norm(foundation), n


def _order(c: Dict[str, Any]):
    d = c.get("posted_date") or date.min
    try:
        sid = int(c.get("src_id"))
    except (TypeError, ValueError):
        sid = 0
    return d, sid


def merge_candidates(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """같은 키의 공고를 묶는다. 대표(가장 최근) 공고에 merged_from(이전 공고 요약)을 붙여 돌려준다."""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in cands:
        groups.setdefault(group_key(c), []).append(c)
    out: List[Dict[str, Any]] = []
    for members in groups.values():
        members = sorted(members, key=_order, reverse=True)
        rep = dict(members[0])
        rep["merged_from"] = [{"src_id": m.get("src_id"), "title": m.get("title"),
                               "apply_end": m["apply_end"].isoformat() if m.get("apply_end") else None}
                              for m in members[1:]]
        out.append(rep)
    return sorted(out, key=_order, reverse=True)
