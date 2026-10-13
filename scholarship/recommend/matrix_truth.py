# -*- coding: utf-8 -*-
"""[RECOMMEND] 4-B2 정답 규칙: 공고 20건의 원문(드림스폰 본문)을 읽고 사람이 만든 판정 기준.

엔진(matcher·places)의 코드를 쓰지 않는다. 원문 조건만 독립적으로 구현해, 엔진이 틀린 곳을 찾는 기준으로 쓴다.
결과는 세 가지다.
  NO      학생이 확실히 대상이 아니다 (엔진은 반드시 '미달'이어야 한다)
  UNSURE  학생 정보로는 판단할 수 없는 조건이 남는다 (문장 조건·공고문 확인·입력 항목 없음). 엔진은 '미달'이면 안 되고 '충족'으로 단정해도 안 된다
  YES     원문의 모든 조건을 학생 정보로 확인했고 충족이다 (엔진은 '충족'이 맞고 '조건부'도 허용)
태그: detail-place(읍·면·동 세부 지역: 코드가 판정하지 않는 설계), ai-gap(AI 구조화가 놓친 원문 조건), ambiguous(원문 해석이 갈림)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

NO, UNSURE, YES = "NO", "UNSURE", "YES"
UNIV, GRAD = "UNIV", "GRAD"
CAPITAL = ("서울특별시", "경기도", "인천광역시")
REGULAR_TERMS = 8


def tri_and(*xs: Optional[bool]) -> Optional[bool]:
    if any(x is False for x in xs):
        return False
    return True if all(x is True for x in xs) else None


def tri_or(*xs: Optional[bool]) -> Optional[bool]:
    if any(x is True for x in xs):
        return True
    return False if xs and all(x is False for x in xs) else None


@dataclass
class Truth:
    outcome: str
    reasons: List[str] = field(default_factory=list)
    tags: Set[str] = field(default_factory=set)


class Judge:
    def __init__(self) -> None:
        self.no: List[str] = []
        self.unsure: List[str] = []
        self.tags: Set[str] = set()

    def fail(self, why: str, tag: Optional[str] = None) -> None:
        self.no.append(why)
        if tag:
            self.tags.add(tag)

    def doubt(self, why: str, tag: Optional[str] = None) -> None:
        self.unsure.append(why)
        if tag:
            self.tags.add(tag)

    def tri(self, value: Optional[bool], what: str, tag: Optional[str] = None) -> None:
        if value is False:
            self.fail(what + " 미충족", tag)
        elif value is None:
            self.doubt(what + " 확인 불가", tag)

    def result(self) -> Truth:
        if self.no:
            return Truth(NO, self.no, self.tags)
        return Truth(UNSURE, self.unsure, self.tags) if self.unsure else Truth(YES, [], self.tags)


class Facts:
    """학생 프로필(StudentProfile)과 학교 설정에서 정답 규칙이 쓰는 사실. 모르면 None."""

    def __init__(self, p, school: Dict[str, object]) -> None:
        self.p = p
        self.school = school

    # ------------------------------------------------------------ 학적
    @property
    def level(self) -> Optional[str]:
        return self.p.level

    @property
    def new_student(self) -> Optional[bool]:
        """신입생 = 한 학기도 이수하지 않은 학생 (편입생 제외). 직전학기 성적이 없어 성적 조건이 적용되지 않는다."""
        if self.p.admission_type == "TRANSFER":
            return False
        if self.p.semesters_completed is None:
            return None
        return self.p.semesters_completed == 0

    @property
    def enrolled(self) -> Optional[bool]:
        return None if self.p.status is None else self.p.status == "ENROLLED"

    @property
    def remaining(self) -> Optional[int]:
        """졸업까지 남은 정규학기 (4년제 학부만)"""
        if self.level != UNIV or self.school["type"] != "GENERAL" or self.p.semesters_completed is None:
            return None
        return REGULAR_TERMS - self.p.semesters_completed

    # ------------------------------------------------------------ 성적
    def gpa(self, which: str, pairs: Sequence[Tuple[float, float]]) -> Optional[bool]:
        """which: last(직전학기) / cum(누적). pairs: [(기준, 만점)]. 학생 만점과 같은 기준이 있을 때만 비교한다."""
        v = self.p.gpa_last if which == "last" else self.p.gpa_cumulative
        scale = self.p.gpa_scale
        if v is None or scale is None:
            return None
        res = [v >= mn for mn, sc in pairs if sc == scale]
        return None if not res else any(res)

    def credits_ge(self, n: float) -> Optional[bool]:
        return None if self.p.credits_last is None else self.p.credits_last >= n

    def percent_ge(self, n: float) -> Optional[bool]:
        return None if self.p.gpa_percent is None else self.p.gpa_percent >= n

    def cumulative_percent_ge(self, n: float) -> Optional[bool]:
        return None if self.p.gpa_cumulative_percent is None else self.p.gpa_cumulative_percent >= n

    def last_n_average_ge(self, n: int, pairs: Sequence[Tuple[float, float]]) -> Optional[bool]:
        rows = self.p.term_history or []
        scale = self.p.gpa_scale
        if len(rows) < n or scale is None:
            return None
        rows = rows[:n]
        simple = sum(r["gpa"] for r in rows) / n
        weighted = sum(r["gpa"] * r["credits"] for r in rows) / sum(r["credits"] for r in rows)
        res = []
        for mn, sc in pairs:
            if sc == scale:
                a, b = simple >= mn, weighted >= mn
                res.append(a if a == b else None)                  # 학점 가중과 단순 평균이 다르게 판정되면 불확실
        if not res:
            return None
        return True if any(x is True for x in res) else (False if all(x is False for x in res) else None)

    # ------------------------------------------------------------ 소득·신분
    def income_le(self, n: int) -> Optional[bool]:
        return None if self.p.income_bracket is None else self.p.income_bracket <= n

    def special(self, *codes: str) -> Optional[bool]:
        have = self.p.special_codes or []
        if any(c in have for c in codes):
            return True
        return False if self.p.special_complete else None

    # ------------------------------------------------------------ 지역
    def _place(self, which: str) -> Tuple[Optional[str], Optional[str]]:
        p = self.p
        return {"residence": (p.residence_sido, p.residence_sigungu), "parent": (p.parent_residence_sido, p.parent_residence_sigungu), "hs": (p.hs_sido, p.hs_sigungu)}[which]

    def place(self, which: str, need: str) -> Optional[bool]:
        """need: '대전광역시' / '경상남도 거창군' / '인천광역시 서구 석남1동'. 시·도, 시·군·구, 읍·면·동 순서로 이름을 그대로 비교한다."""
        sido, sigungu = self._place(which)
        toks = need.split()
        if sido is None:
            return None
        if sido != toks[0]:
            return False
        if len(toks) == 1:
            return True
        have = (sigungu or "").split()
        if not have:
            return None
        if have[0] != toks[1]:
            return False
        if len(toks) == 2:
            return True
        if len(have) < 2:
            return None
        return have[1] == toks[2]

    def residence_years_ge(self, need: float) -> Optional[bool]:
        """본인 거주 기간(구간 하한·상한). 하한이 기준 이상이면 충족, 상한이 기준 미만이면 미충족, 그 사이는 알 수 없다."""
        lo, hi = self.p.residence_years, self.p.residence_years_upper
        if lo is None:
            return None
        if lo >= need:
            return True
        return False if hi is not None and hi < need else None

    def school_sido(self) -> str:
        return str(self.school["sido"])


def _age(f: Facts, j: Judge, mx: Optional[int] = None, mn: Optional[int] = None) -> None:
    a = f.p.age
    if a is None:
        j.doubt("나이 정보 없음")
    elif (mx is not None and a > mx) or (mn is not None and a < mn):
        j.fail("나이 기준 초과·미달 (만 %d세)" % a)


def _major(f: Facts, j: Judge, groups: Set[str]) -> None:
    g = f.p.major_group
    j.tri(None if g is None else g in groups, "학과 계열 %s" % "/".join(sorted(groups)))


# ================================================================== 공고별 정답 규칙 (원문 기준)
def r9053(f: Facts, j: Judge) -> None:                      # 노원교육복지재단: 대학생 만 29세 이하, 노원구 거주, 의과대학생, 전체 학기 3.5/4.5, 중위소득 120%·재산
    if f.level != UNIV:
        j.fail("대학생 대상")
    _age(f, j, mx=29)
    j.tri(f.place("residence", "서울특별시 노원구"), "노원구 거주")
    _major(f, j, {"MEDICAL"})
    if f.new_student is not True:
        j.tri(f.gpa("cum", [(3.5, 4.5)]), "전체 학기 성적 3.5/4.5 이상")
    j.doubt("기준중위소득 120% 이내·가구 재산 기준(지원구간으로 환산 불가)")


def r9058(f: Facts, j: Judge) -> None:                      # 서울미래인재재단: 서울 소재 대학 재(휴)학생, (수급자·차상위 or 1~4구간), 전체 백분위 85
    if f.level != UNIV:
        j.fail("대학생 대상")
    j.tri(f.school_sido() == "서울특별시", "서울 소재 대학")
    j.tri(tri_or(f.special("BASIC_LIVELIHOOD", "NEAR_POOR"), f.income_le(4)), "기초수급자·차상위 또는 지원구간 1~4구간")
    if f.new_student is not True:
        j.tri(f.cumulative_percent_ge(85), "전체 학년 평점 평균 백분위 85점(누적 백분위)")


def r9066(f: Facts, j: Judge) -> None:                      # 대전시: 4년제 대학 신입생, 1년 이상 대전 거주, 대전 소재 대학, 대전 고교 졸업, 공학·자연계열, 내신
    if f.level != UNIV:
        j.fail("4년제 대학 학부생 대상")
    j.tri(f.school["type"] == "GENERAL", "4년제 대학")
    ns = f.new_student
    if ns is False:
        j.fail("신입생만 대상")
    elif ns is None:
        j.doubt("신입생 여부 확인 불가")
    j.tri(tri_and(f.place("residence", "대전광역시"), f.residence_years_ge(1)), "본인 1년 이상 대전 거주")
    j.tri(f.school_sido() == "대전광역시", "대전 소재 대학")
    j.tri(f.place("hs", "대전광역시"), "대전 소재 고교 졸업")
    _major(f, j, {"ENGINEERING", "NATURAL"})
    j.doubt("수학·과학 관련 과목 내신 성적 기준")


def r9071(f: Facts, j: Judge) -> None:                      # 경기도민장학회: 부 or 모 3년 이상 경기도 연속 거주, 재학생 직전학기 12학점·B(3.0), 신입생 수능·내신
    if f.level == GRAD:
        j.fail("고등학생·대학생·전문대생 대상")
    parent = f.place("parent", "경기도")
    if parent is False:
        j.fail("부모 경기도 거주 미충족")
    else:
        j.doubt("부모 3년 이상 연속 거주(입력 항목 없음)" if parent else "부모 거주지 확인 불가")
    ns = f.new_student
    if ns is True:
        j.doubt("신입생: 수능·내신 성적 기준")
    elif ns is None:
        j.doubt("신입생 여부 확인 불가")
    else:
        j.tri(f.credits_ge(12), "직전학기 12학점 이상")
        j.tri(f.gpa("last", [(3.0, 4.5), (3.0, 4.3)]), "직전학기 평점 B(3.0) 이상")
    j.doubt("유형별(장학생·특기생) 선발조건은 공고문")


def r9088(f: Facts, j: Judge) -> None:                      # 한국장학재단 지역인재: 비수도권 소재 대학, 지원 9구간까지, 신규선발 유형별 조건은 공고문
    if f.level != UNIV:
        j.fail("대학생 대상")
    j.tri(f.school_sido() not in CAPITAL, "비수도권 소재 대학")
    j.tri(f.income_le(9), "학자금 지원 9구간 이내")
    j.doubt("참여대학 여부·신규선발 유형별 조건은 공고문")


def r9096(f: Facts, j: Judge) -> None:                      # 거창군: 대학생·전문대생 만 30세 미만, 지역(본인 3년 거주 / 졸업 / 부모 3년 거주), 직전학기 12학점·2.5/4.5
    if f.level != UNIV:
        j.fail("대학생·전문대생 대상")
    _age(f, j, mx=29)
    own = tri_and(f.place("residence", "경상남도 거창군"), f.residence_years_ge(3))
    hs_place = f.place("hs", "경상남도 거창군")
    grad = None if hs_place is not False else False                       # 거창 중·고교 '3년 재학 후 졸업'은 재학 기간을 알 수 없다
    parent_place = f.place("parent", "경상남도 거창군")
    parent = None if parent_place is not False else False                 # 부모 3년 이상 거주 기간은 입력 항목이 없다
    j.tri(tri_or(own, grad, parent), "거창군 지역 조건(본인 3년 거주 / 거창 중·고교 졸업 / 부모 3년 거주)")
    if f.new_student is not True:
        j.tri(f.credits_ge(12), "직전학기 12학점 이상")
        j.tri(f.gpa("last", [(2.5, 4.5)]), "직전학기 평점 2.5/4.5 이상")


def r9098(f: Facts, j: Judge) -> None:                      # 사랑의 날개: 대학생, 사이버·방송통신·사내대학 제외, 장애인, 자기소개서 등 문장 조건
    if f.level != UNIV:
        j.fail("대학생 대상")
    j.tri(f.special("DISABLED"), "장애인")
    j.doubt("학업 우수·봉사정신·경제적 부담·자기소개서 문장 조건")


def r9120(f: Facts, j: Judge) -> None:                      # OK배정장학재단: 남은 정규학기 2학기 이상, 전체 평점 4.0/4.5(3.7/4.3), 지원 8구간 이내, 등록금 해결
    if f.level not in (UNIV, GRAD):
        j.fail("대학생·대학원생 대상")
    if f.level == UNIV:
        rem = f.remaining
        j.tri(None if rem is None else rem >= 2, "정규학기 2학기 이상 남음")
        if f.new_student is not True:
            j.tri(f.gpa("cum", [(4.0, 4.5), (3.7, 4.3)]), "전체 평점 4.0/4.5(3.7/4.3) 이상")
    else:
        j.doubt("대학원생: 석사·학부 각각 평점 기준")
    j.tri(f.income_le(8), "지원 8구간 이내")
    j.doubt("등록금 전액을 장학금 등으로 해결한 자")


def r9141(f: Facts, j: Judge) -> None:                      # 이영주한중인재양성장학재단: 한국 국적, 중국 대학·대학원 입학예정자·재학생
    j.fail("중국 소재 대학 재학·입학예정자만 대상 (본교는 국내)")
    if f.p.nationality is not None and f.p.nationality != "KR":
        j.fail("한국 국적자 대상")


def r9144(f: Facts, j: Judge) -> None:                      # 이공계 국가우수장학: 신규장학생으로 선발된 3학년 진급 재학생, 공학·자연계열, 직전 4개 학기
    if f.level != UNIV:
        j.fail("대학생 대상")
    if f.enrolled is False:
        j.fail("재학생 대상")
    g = f.p.grade
    if g is None:
        j.doubt("학년 확인 불가")
    elif g in (2, 3):
        j.doubt("'3학년 진급 재학생'은 현재 2학년을 뜻할 수도, 3학년을 뜻할 수도 있다", "ambiguous")
    else:
        j.fail("3학년 진급 재학생이 아님 (학년 %d)" % g)
    _major(f, j, {"ENGINEERING", "NATURAL"})
    j.tri(f.last_n_average_ge(4, [(3.5, 4.5), (3.3, 4.3)]), "직전 4개 학기 평점 평균 3.5/4.5(3.3/4.3) 이상 또는 백분위 87")
    j.doubt("신규장학생으로 선발된 자")


def r9163(f: Facts, j: Judge) -> None:                      # 인천 중소기업 노동자 자녀: 부모 인천 1년 거주·근무, 중소기업, 재학생 직전학기 70점 or C학점
    if f.level != UNIV:
        j.fail("대학생 대상")
    parent = f.place("parent", "인천광역시")
    if parent is False:
        j.fail("부모 인천 거주 미충족")
    else:
        j.doubt("부모 1년 이상 거주·인천 소재 중소기업 근무")
    ns = f.new_student
    if ns is True:
        j.doubt("신입생: 입학성적 상위 50%")
    elif ns is None:
        j.doubt("신입생 여부 확인 불가")
    else:
        j.tri(tri_or(f.percent_ge(70), f.gpa("last", [(2.0, 4.5), (2.0, 4.3)])), "직전학기 평균 70점 이상 또는 C학점(2.0) 이상")


def r9178(f: Facts, j: Judge) -> None:                      # 광주 동구: 본인과 부모가 동구에 1년 이상 거주, 유형별 조건은 공고문
    if f.level == GRAD:
        j.fail("고등학생·대학생·전문대생 대상")
    own = tri_and(f.place("residence", "광주광역시 동구"), f.residence_years_ge(1))
    parent_place = f.place("parent", "광주광역시 동구")
    parent = None if parent_place is not False else False
    j.tri(tri_and(own, parent), "본인과 부모 모두 동구 1년 이상 거주")
    j.doubt("유형별 선발조건은 공고문")


def r9181(f: Facts, j: Judge) -> None:                      # 원덕읍: 본인 or 부모 3년 이상 원덕읍 거주, 원덕읍 중·고교 졸업 (두 조건이 '또는'인지 '그리고'인지 모호)
    if f.level == GRAD:
        j.fail("고등학생·대학생·전문대생 대상")
    town = "강원특별자치도 삼척시 원덕읍"
    own = tri_and(f.place("residence", town), f.residence_years_ge(3))
    pp = f.place("parent", town)
    parent = None if pp is not False else False
    grad = f.place("hs", town)
    any_reading = tri_or(own, parent, grad)
    and_reading = tri_and(tri_or(own, parent), grad)
    if any_reading is False and and_reading is False:
        j.fail("원덕읍 거주·졸업 조건 미충족", "detail-place")
    elif any_reading is True and and_reading is True:
        pass
    else:
        j.doubt("원덕읍 거주 3년/졸업 조건의 결합 방식 또는 정보 부족", "detail-place")


def r9195(f: Facts, j: Judge) -> None:                      # 국가장학금: 지원 9구간 이하 재학·신입·편입생(재입학·복학 가능), 유형별 성적, Ⅱ유형·지역인재는 대학별 기준
    if f.level != UNIV:
        j.fail("대학생 대상")
    if f.enrolled is False:
        j.fail("재학 중인 학생 대상 (휴학 제외)")
    j.tri(f.income_le(9), "학자금 지원 9구간 이내")
    j.doubt("국가장학금Ⅱ유형(대학별)·지역인재장학금(입학성적) 선발 기준")


def r9210(f: Facts, j: Judge) -> None:                      # 춘천시 봄내장학: 본인 or 부모 1년 이상 거주 (주소이전·세인트존스대 예외), 유형별 조건은 공고문
    if f.level == GRAD:
        j.fail("고등학생·대학생 대상")
    j.doubt("유형별 선발조건·예외(주소이전, 세인트존스대학교)는 공고문")


def r9211(f: Facts, j: Judge) -> None:                      # 대전시 인재육성장학: 유형별 조건은 공고문
    if f.level == GRAD:
        j.fail("고등학생·대학생 대상")
    j.doubt("유형별 선발조건은 공고문")


def r9212(f: Facts, j: Judge) -> None:                      # 문주장학재단: 2학년 이상 학부 재학생, 본인 or 부모 1년 이상 서초구, 공학계열(ICT 관련), 직전학기 3.0 & 12학점
    if f.level != UNIV:
        j.fail("학부생 대상")
    if f.enrolled is False:
        j.fail("재학생 대상")
    g = f.p.grade
    if g is None:
        j.doubt("학년 확인 불가")
    elif g < 2:
        j.fail("2학년 이상 대상")
    own = tri_and(f.place("residence", "서울특별시 서초구"), f.residence_years_ge(1))
    pp = f.place("parent", "서울특별시 서초구")
    parent = None if pp is not False else False
    j.tri(tri_or(own, parent), "본인 또는 부모 서초구 1년 이상 거주")
    _major(f, j, {"ENGINEERING"})
    j.tri(f.gpa("last", [(3.0, 4.5), (3.0, 4.3)]), "직전학기 평점 3.0 이상")
    j.tri(f.credits_ge(12), "직전학기 12학점 이상")
    if f.p.major_group == "ENGINEERING":
        j.doubt("AI·빅데이터 등 ICT 관련 전공자")


def r9217(f: Facts, j: Judge) -> None:                      # 인천 SK행복: 대상 동 거주, 특수계층(수급·차상위·한부모·중증장애), 대학생 직전학기 2.5/4.5·지원 6구간
    if f.level == GRAD:
        j.fail("고등학생·대학생 대상")
    dongs = ["인천광역시 서구 " + d for d in ("신현원창동", "석남1동", "석남2동", "석남3동", "가정1동", "가좌1동", "가좌2동", "가좌3동")]
    region = tri_or(*[f.place("residence", d) for d in dongs], *[f.place("parent", d) for d in dongs])
    if region is False:
        j.fail("대상 동 거주 미충족", "detail-place" if (f.p.residence_sido == "인천광역시" and (f.p.residence_sigungu or "").startswith("서구")) else None)
    elif region is None:
        j.doubt("대상 동 거주 확인 불가", "detail-place")
    special = f.special("BASIC_LIVELIHOOD", "NEAR_POOR", "SINGLE_PARENT", "DISABLED")
    income = f.income_le(6)
    both, either = tri_and(special, income), tri_or(special, income)
    if both is False and either is False:
        j.fail("특수계층 및 소득 6구간 미충족")
    elif not (both is True and either is True):
        j.doubt("특수계층과 소득 6구간이 '그리고'인지 '또는'인지 모호하거나 정보 부족", "ambiguous")
    if f.p.admission_type == "TRANSFER" and (f.p.semesters_completed or 0) <= 1:
        j.doubt("편입 후 첫학기: 편입 전 학교 성적")
    elif f.new_student is not True:
        j.tri(f.gpa("last", [(2.5, 4.5), (2.3, 4.3)]), "직전학기 평점 2.5/4.5(2.3/4.3) 이상")


def r9227(f: Facts, j: Judge) -> None:                      # 선원가족장학: 선원 및 가족, 대학생 직전학기 평균 2.0 이상
    if f.level == GRAD:
        j.fail("고등학생·대학생 대상")
    if f.new_student is not True:
        j.tri(f.gpa("last", [(2.0, 4.5), (2.0, 4.3)]), "직전학기 평균 2.0 이상")
    j.doubt("선원법 적용 선박 승선 선원 및 가족, 승무경력, 월소득 기준")


def r9237(f: Facts, j: Judge) -> None:                      # 안중근의사숭모회: 2학년 2학기~3학년 2학기 재학생, 직전학기 15학점, 전체 평균 3.6
    if f.level != UNIV:
        j.fail("대학생 대상")
    if f.enrolled is False:
        j.fail("재학생 대상")
    c = f.p.semesters_completed
    if c is None:
        j.doubt("이수학기 확인 불가")
    elif not 3 <= c <= 5:
        j.fail("2학년 2학기~3학년 2학기(이수 3~5학기)가 아님 (이수 %d학기)" % c)
    j.tri(f.credits_ge(15), "직전학기 15학점 이상")
    j.tri(f.gpa("cum", [(3.6, 4.5)]), "전체 성적 평균 3.6 이상")
    # [기타조건] '역사에 관심이 많고 활동 실적·계획이 있는 자'는 심사 기준(REVIEW)으로 분류해 확인 사항에 따로 표시한다 (제품 결정: 판정에는 영향 없음)


RULES: Dict[str, Callable[[Facts, Judge], None]] = {
    "9053": r9053, "9058": r9058, "9066": r9066, "9071": r9071, "9088": r9088, "9096": r9096, "9098": r9098, "9120": r9120, "9141": r9141, "9144": r9144,
    "9163": r9163, "9178": r9178, "9181": r9181, "9195": r9195, "9210": r9210, "9211": r9211, "9212": r9212, "9217": r9217, "9227": r9227, "9237": r9237}


def judge(idx: str, facts: Facts) -> Truth:
    j = Judge()
    RULES[idx](facts, j)
    return j.result()
