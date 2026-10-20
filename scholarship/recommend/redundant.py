# -*- coding: utf-8 -*-
"""[RECOMMEND] 이미 판정한 조건과 같은 뜻의 문장 조건을 확인 필요 목록에서 뺀다.

AI 추출은 같은 조건을 구조화 값(학교급·학년·이수 학기·평점)과 문장(other_conditions)으로 한 번씩 남기는 경우가 있다
("국내 4년제 대학 재학생", "학사 2년 이상을 마쳐야 하며"). 구조화 값은 이미 판정하므로 문장까지 확인 필요로 보이면 학생에게는 겹치는 항목이다.

안전 규칙 (조건을 숨겨서 거짓 통과가 생기지 않게):
- 그 공고에 해당 구조화 값이 **있을 때만** 그 종류의 어휘를 허용한다 (학교급 값이 없으면 '대학 재학생'은 어휘가 아니다).
- 문장을 허용 어휘와 조사·숫자·기호까지 지웠을 때 **아무것도 남지 않아야** 겹치는 문장이다. 모르는 단어가 하나라도 남으면 그대로 확인 필요로 둔다.
- 부정·제한 표현(제외·불가·아닌·없는·미만·이하·초과·진급)이 있으면 어휘와 상관없이 그대로 둔다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

_BLOCK = re.compile(r"제외|불가|아닌|아니|없는|없이|미만|이하|초과|진급|단,|다만|단\s|예외|우선")
_FILLER = ("자격", "대상", "지원자", "지원", "가능", "사람", "모두", "또는", "및", "하는", "하며", "하고", "하여", "해야", "합니다", "입니다", "이어야", "되어야",
           "해당", "한다", "인", "중", "자", "분", "등", "은", "는", "이", "가", "을", "를", "의", "에", "서", "로", "으로", "과", "와", "한", "된", "된다", "야", "며", "에는", "에서는")
_PUNCT = re.compile(r"[\s\d.,;:()\[\]{}<>·ㆍ・~\-–—/\\'\"“”‘’※*#%+=?!]+")


def _classes(elig: Dict[str, Any]) -> List[str]:
    """이 공고(또는 유형)가 구조화 값으로 이미 가진 조건의 종류별 허용 어휘."""
    words: List[str] = []
    levels = elig.get("target_levels") or []
    if "UNIV" in levels:
        words += ["대학교", "대학생", "대학", "학부생", "학부", "재학생", "재학", "학생", "국내", "정규"]
    types = (elig.get("school") or {}).get("types") or []
    if "UNIV4" in types:
        words += ["4년제대학교", "4년제대학", "4년제", "일반대학", "년제"]
    if "COLLEGE" in types:
        words += ["전문대학", "전문대", "년제"]
    if elig.get("grades"):
        words += ["학년", "이상", "재학생", "재학", "학생"]
    ct = elig.get("completed_terms") or {}
    if isinstance(ct, dict) and ct.get("min") is not None:
        words += ["학사", "학기", "이수", "마쳐", "마친", "마치", "수료", "년", "이상", "수혜", "시점", "장학금", "재학"]
    gpa = elig.get("gpa") or {}
    if isinstance(gpa, dict) and gpa.get("min") is not None:
        words += ["평점", "평균", "누계", "누적", "직전", "학기", "성적", "이상", "학점"]
    return words


def is_redundant(text: str, scopes: Iterable[Dict[str, Any]]) -> bool:
    """scopes: 공고 전체 + (유형 문장이면) 그 유형의 구조화 값."""
    s = (text or "").strip()
    if not s or _BLOCK.search(s):
        return False
    vocab: List[str] = []
    for sc in scopes:
        vocab += _classes(sc)
    if not vocab:
        return False
    for w in sorted(set(vocab) | set(_FILLER), key=len, reverse=True):
        s = s.replace(w, "")
    return _PUNCT.sub("", s) == ""


def drop_redundant(texts: Iterable[str], *scopes: Dict[str, Any]) -> List[str]:
    return [t for t in texts if not is_redundant(t, scopes)]
