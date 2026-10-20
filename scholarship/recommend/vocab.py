# -*- coding: utf-8 -*-
"""[RECOMMEND] AI 가 쓴 표현 <-> 학생 프로필 값 대응. 표현이 달라서 비교가 안 되는 문제를 한곳에서 해결한다."""
from __future__ import annotations

import re
from typing import Optional, Set, Tuple

# ------------------------------------------------------------------ 학적 상태 문구 -> 태그
ENROLLED, LEAVE, GRADUATED, GRADUATING = "ENROLLED", "LEAVE", "GRADUATED", "GRADUATING"
EXTENDED, DEFERRED, GRAD_STUDENT, REMOTE = "EXTENDED", "DEFERRED", "GRAD_STUDENT", "REMOTE"
TUITION_UNPAID, NEW_STUDENT, TRANSFER = "TUITION_UNPAID", "NEW_STUDENT", "TRANSFER"


def status_tags(text: str) -> Set[str]:
    t = re.sub(r"\s+", "", text or "")
    tags: Set[str] = set()
    if "휴학" in t:
        tags.add(LEAVE)
    if re.search(r"졸업학기|졸업예정|마지막학기", t):
        tags.add(GRADUATING)
    elif re.search(r"졸업|수료", t) and not re.search(r"졸업유예|졸업연기|졸업어학|졸업요건", t):
        tags.add(GRADUATED)
    if re.search(r"초과학기|초과등록", t):
        tags.add(EXTENDED)
    if re.search(r"유예|졸업연기|0학점", t):
        tags.add(DEFERRED)
    if "대학원" in t:
        tags.add(GRAD_STUDENT)
    if re.search(r"사이버|원격|방송통신|방송대|통신대|학점은행", t):
        tags.add(REMOTE)
    if re.search(r"등록금.*(미납|분납)|미납|분납", t):
        tags.add(TUITION_UNPAID)
    if "신입생" in t:
        tags.add(NEW_STUDENT)
    if "편입생" in t:
        tags.add(TRANSFER)
    if "재학" in t:
        tags.add(ENROLLED)        # 제외 대상 문구에서는 matcher 가 이 태그를 무시한다 (예: '졸업연기자(0학점 재학생)')
    return tags


# ------------------------------------------------------------------ 지역명
_SIDO = {
    "서울": ("서울", "서울시", "서울특별시"), "부산": ("부산", "부산시", "부산광역시"), "대구": ("대구", "대구시", "대구광역시"),
    "인천": ("인천", "인천시", "인천광역시"), "광주": ("광주", "광주시", "광주광역시"), "대전": ("대전", "대전시", "대전광역시"),
    "울산": ("울산", "울산시", "울산광역시"), "세종": ("세종", "세종시", "세종특별자치시"),
    "경기": ("경기", "경기도"), "강원": ("강원", "강원도", "강원특별자치도"),
    "충북": ("충북", "충청북도"), "충남": ("충남", "충청남도"), "전북": ("전북", "전라북도", "전북특별자치도"),
    "전남": ("전남", "전라남도"), "경북": ("경북", "경상북도"), "경남": ("경남", "경상남도"),
    "제주": ("제주", "제주도", "제주특별자치도"),
}
_SIDO_ALIAS = {alias: core for core, aliases in _SIDO.items() for alias in aliases}
_SUFFIX = re.compile(r"(특별자치도|특별자치시|특별시|광역시|시|군|구)$")


_SIDO_OFFICIAL = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
                  "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
                  "제주": "제주특별자치도"}
SIDO_OFFICIAL_NAMES = tuple(_SIDO_OFFICIAL.values())                                  # 17개 시·도의 공식 이름


def sido_core(text: Optional[str]) -> Optional[str]:
    t = re.sub(r"\s+", "", text or "")
    return _SIDO_ALIAS.get(t)


def canon_sido(text: Optional[str]) -> Optional[str]:
    """'경기' / '경기도' / '대구시' / '강원도'(옛 이름) -> 공식 이름('경기도', '대구광역시', '강원특별자치도'). 시·도가 아니면 None."""
    core = sido_core(text)
    return _SIDO_OFFICIAL.get(core) if core else None


def place_core(text: Optional[str]) -> Optional[str]:
    """'춘천시' -> '춘천', '시흥시' -> '시흥', '강남구' -> '강남'. 시·도 이름이면 시·도 코어('서울')."""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return None
    if t in _SIDO_ALIAS:
        return _SIDO_ALIAS[t]
    core = _SUFFIX.sub("", t)
    return core if len(core) >= 2 else t


def classify_place(item: str) -> Tuple[str, Optional[str]]:
    """공고의 지역 항목 -> ('SIDO'|'SIGUNGU'|'OTHER', 코어). 리·동·면·읍 단위는 'OTHER' (판정 불가)."""
    t = re.sub(r"\s+", "", item or "")
    if t in _SIDO_ALIAS:
        return "SIDO", _SIDO_ALIAS[t]
    if re.search(r"(시|군|구)$", t) and not re.search(r"(리|동|면|읍)$", t):
        return "SIGUNGU", place_core(t)
    return "OTHER", None


# ------------------------------------------------------------------ 소득 유형
BASIC, NEAR_POOR, SINGLE_PARENT, BRACKET, OTHER = "BASIC", "NEAR_POOR", "SINGLE_PARENT", "BRACKET", "OTHER"


def income_kind(text: str) -> str:
    t = re.sub(r"\s+", "", text or "")
    if re.search(r"차상위", t):
        return NEAR_POOR
    if re.search(r"기초생활|수급자|기초수급", t):
        return BASIC
    if re.search(r"한부모", t):
        return SINGLE_PARENT
    if re.search(r"구간|분위", t):
        return BRACKET
    return OTHER


def parse_bracket(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*(?:구간|분위)", text or "")
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ 국적
def nationality_kind(text: Optional[str]) -> Optional[str]:
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return None
    if re.search(r"외국인|유학생|외국국적", t):
        return "FOREIGN"
    if re.search(r"대한민국|한국|내국인|국민", t):
        return "KR"
    return None


# ------------------------------------------------------------------ 거주 기간 표현
_DURATION = re.compile(r"(\d+)\s*(년(?!\s*제)|개월)")                    # '4년제 대학' 의 4년은 기간이 아니다


def durations(text: Optional[str]):
    """텍스트 안의 '3년', '6개월' 같은 기간 표현 목록 (개월 단위로 환산)."""
    out = []
    for n, unit in _DURATION.findall(text or ""):
        out.append(int(n) * (12 if unit == "년" else 1))
    return out
