# -*- coding: utf-8 -*-
"""[RECOMMEND] 지명 비교: 시·도 / 시·군·구 / 일반구(시 아래의 구).

공고(v3)는 지역을 '서울특별시 서초구', '경기도 수원시 영통구'처럼 시·도와 시·군·구를 한 문자열에 적는다.
이를 분해해서 비교한다. 학생이 시·군까지만 적었는데 공고가 구까지 요구하면 '확인 필요'(UNKNOWN)이고,
같은 이름이 여러 시·도에 있는 시·군·구(중구, 동구, 광주 ...)는 시·도 없이는 확정하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import vocab

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# 일반구(시 아래의 구): 구 코어 -> (시 코어, 시·도 코어). 광역시의 자치구와 이름이 겹치지 않는 것만 둔다 (포항 남구·북구 등은 '시 + 구'로 적힌 경우만 읽는다).
GENERAL_GU = {
    "장안": ("수원", "경기"), "권선": ("수원", "경기"), "팔달": ("수원", "경기"), "영통": ("수원", "경기"),
    "수정": ("성남", "경기"), "중원": ("성남", "경기"), "분당": ("성남", "경기"),
    "만안": ("안양", "경기"), "동안": ("안양", "경기"), "상록": ("안산", "경기"), "단원": ("안산", "경기"),
    "덕양": ("고양", "경기"), "일산동": ("고양", "경기"), "일산서": ("고양", "경기"),
    "처인": ("용인", "경기"), "기흥": ("용인", "경기"), "수지": ("용인", "경기"),
    "의창": ("창원", "경남"), "성산": ("창원", "경남"), "마산합포": ("창원", "경남"), "마산회원": ("창원", "경남"), "진해": ("창원", "경남"),
    "상당": ("청주", "충북"), "서원": ("청주", "충북"), "흥덕": ("청주", "충북"), "청원": ("청주", "충북"),
    "동남": ("천안", "충남"), "서북": ("천안", "충남"), "완산": ("전주", "전북"), "덕진": ("전주", "전북"),
}
# 시·도 없이는 어느 곳인지 알 수 없는 시·군·구 코어 (같은 이름이 여러 시·도에 있음)
AMBIGUOUS_CITY = {"중구", "동구", "서구", "남구", "북구", "강서", "고성", "광주"}

# 권역: 이름 -> 속한 시·도 코어. 공고가 '비수도권 소재 대학'처럼 시·도 대신 권역으로 적는 경우
_ALL_SIDO = frozenset(["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"])
REGION_GROUPS = {
    "수도권": frozenset(["서울", "경기", "인천"]),
    "비수도권": _ALL_SIDO - frozenset(["서울", "경기", "인천"]),
    "충청권": frozenset(["대전", "세종", "충북", "충남"]),
    "호남권": frozenset(["광주", "전북", "전남"]),
    "영남권": frozenset(["부산", "대구", "울산", "경북", "경남"]),
}
# 해외 지명(국가·대륙): 국내 지역에 사는 학생은 해당하지 않는다
FOREIGN_WORDS = frozenset(["해외", "국외", "외국", "중국", "일본", "미국", "영국", "독일", "프랑스", "캐나다", "호주", "대만", "베트남", "싱가포르", "러시아", "인도", "인도네시아",
                           "태국", "필리핀", "몽골", "홍콩", "유럽", "아시아", "북미", "중남미"])
DOMESTIC_WORDS = frozenset(["국내", "대한민국", "한국"])

_SUFFIX_UNIT = re.compile(r"[가-힣0-9]+?(?:특별자치도|특별자치시|특별시|광역시|도|시|군|구|읍|면|동|리|가)")


@dataclass(frozen=True)
class Place:
    sido: Optional[str] = None          # 시·도 코어 ('경기')
    city: Optional[str] = None          # 시·군·자치구 코어 ('수원', '서초', '거창')
    gu: Optional[str] = None            # 일반구 코어 ('영통')
    other: bool = False                 # 읍·면·동·리 같은 세부 단위가 있음 (이름이 다르다고 미달로 보지는 않는다)
    detail: tuple = ()                  # 그 세부 단위의 이름 ('석남1동'). 공고와 학생의 이름이 정확히 같을 때만 충족으로 본다
    ambiguous: bool = False             # 시·도 없이는 확정할 수 없는 이름
    raw: str = ""
    group: Optional[frozenset] = None   # 권역('수도권' 등)이면 그 권역에 속한 시·도 코어 집합
    group_name: str = ""
    foreign: bool = False               # 해외 지명 ('중국', '해외')
    domestic: bool = False              # '국내', '대한민국'

    @property
    def empty(self) -> bool:
        return not (self.sido or self.city or self.gu or self.group or self.foreign or self.domestic)


def _merge_sido(parts: List[str]) -> List[str]:
    """붙여쓰기를 나눈 조각 중 합치면 시·도 이름인 것을 되돌린다: ['대구', '광역시', '수성구'] -> ['대구광역시', '수성구']"""
    out: List[str] = []
    i = 0
    while i < len(parts):
        if i + 1 < len(parts) and vocab.sido_core(parts[i] + parts[i + 1]):
            out.append(parts[i] + parts[i + 1])
            i += 2
        else:
            out.append(parts[i])
            i += 1
    return out


def _tokens(raw: str) -> List[str]:
    out: List[str] = []
    for t in raw.split():
        if vocab.sido_core(t):                                                         # '대구광역시'는 '대구'(구로 끝남)+'광역시'로 쪼개지 않는다
            out.append(t)
            continue
        parts = _SUFFIX_UNIT.findall(t) if len(t) >= 5 else []
        out.extend(_merge_sido(parts) if len(parts) >= 2 and "".join(parts) == t else [t])        # '경기도수원시영통구' 같은 붙여쓰기도 나눔
    return out


def parse_place(raw: Optional[str]) -> Place:
    text = (raw or "").strip()
    key = re.sub(r"(지역|소재지?|소재대학|지방)$", "", re.sub(r"\s+", "", text))
    if key in REGION_GROUPS:
        return Place(group=REGION_GROUPS[key], group_name=key, raw=text)
    toks = _tokens(text)
    if toks and (toks[0] in FOREIGN_WORDS or key in FOREIGN_WORDS):
        return Place(foreign=True, raw=text)
    if key in DOMESTIC_WORDS:
        return Place(domestic=True, raw=text)
    sido = city = gu = None
    other = False
    details: List[str] = []
    for i, t in enumerate(toks):
        core_sido = vocab.sido_core(t)
        if core_sido and sido is None and city is None:
            if t in ("광주", "광주시"):                                   # 광주광역시인지 경기 광주시인지: 뒤에 구·군이 오면 광역시
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if re.search(r"[구군]$", nxt):
                    sido = "광주"
                else:
                    city = "광주"
            else:
                sido = core_sido
            continue
        if re.search(r"(읍|면|동|리|가)$", t) and not re.search(r"(시|군|구)$", t):
            other = True
            details.append(t)
        elif re.search(r"(시|군)$", t):
            if city is None:
                city = vocab.place_core(t)
            else:
                other = True
                details.append(t)
        elif t.endswith("구"):
            core = vocab.place_core(t)
            if city is None:
                if core in GENERAL_GU:                                    # '영통구'만 적은 경우: 수원시 영통구로 본다
                    city, sido = GENERAL_GU[core][0], sido or GENERAL_GU[core][1]
                    gu = core
                else:
                    city = core                                           # 서초구 같은 자치구
            elif gu is None:
                gu = core
            else:
                other = True
                details.append(t)
        else:
            other = True
            details.append(t)
    ambiguous = bool(city in AMBIGUOUS_CITY and sido is None)
    return Place(sido=sido, city=city, gu=gu, other=other, detail=tuple(details), ambiguous=ambiguous, raw=text)


def student_place(sido: Optional[str], sigungu: Optional[str]) -> Place:
    return parse_place(" ".join(s for s in (sido, sigungu) if s))


def _detail_result(req: Place, stu: Place) -> Tuple[str, str]:
    """시·도, 시·군·구가 맞은 뒤의 읍·면·동·리. 이름이 정확히 같을 때만 충족이고, 다르거나 학생이 입력하지 않았으면 확인 필요 (법정동·행정동 표기가 달라 미달로 단정하지 않는다)."""
    if not req.other:
        return PASS, "해당"
    if req.detail and set(req.detail) <= set(stu.detail):
        return PASS, "해당 (세부 지역 이름 일치)"
    return UNKNOWN, "세부 지역(읍·면·동·리) 확인 필요"


def compare(req: Place, stu: Place) -> Tuple[str, str]:
    """공고가 요구하는 지역(req)과 학생 지역(stu) 비교 -> (PASS|FAIL|UNKNOWN, 이유)."""
    if req.group is not None:
        if stu.sido is None:
            return UNKNOWN, "학생의 시·도 정보 없음 ({} 여부를 알 수 없음)".format(req.group_name)
        return (PASS, "{} 해당".format(req.group_name)) if stu.sido in req.group else (FAIL, "{}이 아님".format(req.group_name))
    if req.foreign:
        if stu.sido or stu.city:
            return FAIL, "국내 지역이라 해외 조건에 해당하지 않음"
        return UNKNOWN, "학생의 지역 정보 없음"
    if req.domestic:
        return (PASS, "국내 지역 해당") if (stu.sido or stu.city) else (UNKNOWN, "학생의 지역 정보 없음")
    if req.empty:
        return UNKNOWN, "세부 지역(읍·면·동·리) 확인 필요"
    if req.sido and stu.sido and req.sido != stu.sido:
        return FAIL, "시·도가 다름"
    if req.city:
        if stu.city is None:
            return UNKNOWN, "학생의 시·군·구 정보 없음"
        if req.city == "광주" and req.sido is None and (stu.sido == "광주" or stu.city == "광주"):
            return UNKNOWN, "광주광역시인지 경기 광주시인지 확인 필요"        # 시·도 없이 '광주'만 적힌 경우
        if req.city != stu.city:
            return FAIL, "시·군·구가 다름"
        if req.ambiguous or stu.ambiguous:
            return UNKNOWN, "같은 이름의 시·군·구가 여러 시·도에 있어 시·도 확인 필요"
        if req.gu:
            if stu.gu is None:
                return UNKNOWN, "구 단위까지 확인 필요 (학생이 시·군까지만 입력)"
            if req.gu != stu.gu:
                return FAIL, "구가 다름"
        return _detail_result(req, stu)
    if stu.sido is None:
        return UNKNOWN, "학생의 시·도 정보 없음"
    return _detail_result(req, stu)


def combine_any(statuses: List[str]) -> str:
    """여러 지역 중 하나면 되는 경우: 하나라도 PASS 면 PASS, 전부 FAIL 이면 FAIL, 그 외 UNKNOWN"""
    if not statuses or PASS in statuses:
        return PASS
    return FAIL if all(s == FAIL for s in statuses) else UNKNOWN
