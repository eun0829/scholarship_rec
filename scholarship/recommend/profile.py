# -*- coding: utf-8 -*-
"""[RECOMMEND] 학생 프로필 (개인정보 제외).

판정에 필요한 '속성'만 받는다. 이름, 학번, 주민번호, 연락처, 이메일, 상세 주소 같은 항목이 들어오면
받지 않고 버린다 (dropped_sensitive 로 알려줌). 값이 없는 항목은 None 이며, 판정에서 '알 수 없음'이 된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

from . import vocab
from ..common.ai.schema_v3 import MAJOR_GROUPS, SCHOOL_PROGRAMS, SPECIAL_CODES

LEVELS = ("ELEM", "MIDDLE", "HIGH", "UNIV", "GRAD")
STATUSES = ("ENROLLED", "LEAVE", "GRADUATED")
SCHOOL_TYPES = ("GENERAL", "COLLEGE", "REMOTE", "OTHER")      # 일반대학 / 전문대학 / 원격(사이버·방송통신·학점은행) / 기타
KNOWN_SCALES = (4.5, 4.3, 4.0, 5.0)
ADMISSION_TYPES = ("NEW", "TRANSFER")                          # 신입학 / 편입학
GENDERS = ("M", "F")

# 받지 않는 항목 (소문자로 비교)
SENSITIVE_KEYS = {
    "name", "student_name", "fullname", "student_id", "studentid", "id", "uid", "학번", "이름", "성명",
    "rrn", "ssn", "resident_id", "주민번호", "주민등록번호", "birth", "birthday", "birthdate", "생년월일",
    "phone", "tel", "mobile", "cell", "연락처", "전화", "전화번호", "휴대폰", "email", "mail", "이메일",
    "address", "addr", "street", "주소", "상세주소", "account", "bank", "계좌",
}

_SENSITIVE_EXACT = {re.sub(r"[-_\s]", "", k) for k in SENSITIVE_KEYS}
# 이름 안에 이 조각이 들어 있으면 개인정보로 본다 (home_address, E-mail, phone_number, 학생이름 ...)
_SENSITIVE_PARTS = ("email", "phone", "mobile", "address", "birth", "ssn", "rrn", "residentid", "studentid", "studentname",
                    "fullname", "bankaccount", "accountnumber", "학번", "이름", "성명", "주민", "연락처", "전화", "휴대폰", "이메일",
                    "상세주소", "생년월일", "계좌")


def is_sensitive_key(key: Any) -> bool:
    k = re.sub(r"[-_\s]", "", str(key).lower())
    return k in _SENSITIVE_EXACT or any(part in k for part in _SENSITIVE_PARTS)


_LEVEL_WORDS = {"대학생": "UNIV", "대학": "UNIV", "대학교": "UNIV", "학부": "UNIV", "대학원생": "GRAD", "대학원": "GRAD",
                "고등학생": "HIGH", "고교": "HIGH", "고등학교": "HIGH", "중학생": "MIDDLE", "중학교": "MIDDLE",
                "초등학생": "ELEM", "초등학교": "ELEM"}
_STATUS_WORDS = {"재학": "ENROLLED", "재학생": "ENROLLED", "휴학": "LEAVE", "휴학생": "LEAVE", "졸업": "GRADUATED",
                 "졸업생": "GRADUATED", "수료": "GRADUATED", "수료생": "GRADUATED"}
_SCHOOL_WORDS = {"일반대학": "GENERAL", "일반": "GENERAL", "대학교": "GENERAL", "전문대학": "COLLEGE", "전문대": "COLLEGE",
                 "원격": "REMOTE", "원격대학": "REMOTE", "사이버대학": "REMOTE", "사이버": "REMOTE", "방송통신대": "REMOTE",
                 "방송대": "REMOTE", "학점은행제": "REMOTE", "기타": "OTHER"}


_ADMISSION_WORDS = {"신입학": "NEW", "신입": "NEW", "편입학": "TRANSFER", "편입": "TRANSFER"}
_GENDER_WORDS = {"남": "M", "남자": "M", "남성": "M", "남학생": "M", "여": "F", "여자": "F", "여성": "F", "여학생": "F"}
_PROGRAM_WORDS = {"국가장학금": "NATIONAL_SCHOLARSHIP", "지역인재": "LOCAL_TALENT", "지역인재장학금": "LOCAL_TALENT",
                  "주거안정": "HOUSING_STABILITY", "주거안정장학금": "HOUSING_STABILITY"}
_MAJOR_WORDS = {"인문": "HUMANITIES", "사회": "SOCIAL", "자연": "NATURAL", "공학": "ENGINEERING", "의약": "MEDICAL", "의학": "MEDICAL", "보건": "MEDICAL",
                "예체능": "ARTS_PE", "예술": "ARTS_PE", "교육": "EDUCATION", "농림": "AGRICULTURE", "농업": "AGRICULTURE"}
_TERM = re.compile(r"^\s*((?:19|20)\d{2})\s*(?:[-./]|년)\s*([12])\s*(?:학기)?\s*$")


def normalize_term(v: Any) -> Optional[str]:
    """'2026-1', '2026.1', '2026년 1학기', '2026-1학기' -> '2026-1'. 읽을 수 없으면 None."""
    m = _TERM.match(str(v)) if v is not None else None
    return "{}-{}".format(m.group(1), m.group(2)) if m else None


class ProfileError(ValueError):
    pass


def reason(key: str, err: Exception) -> str:
    """오류 문구 앞의 키 이름 반복을 없앤다 ('age: 정수 …' -> '정수 …'). 호출하는 쪽이 이미 키를 앞에 붙이므로 두 번 나오지 않게 한다."""
    msg = str(err)
    return msg[len(key) + 1:].strip() if msg.startswith(key + ":") else msg


@dataclass
class StudentProfile:
    level: Optional[str] = None                 # ELEM/MIDDLE/HIGH/UNIV/GRAD
    grade: Optional[int] = None                 # 학년
    status: Optional[str] = None                # ENROLLED/LEAVE/GRADUATED
    final_semester: Optional[bool] = None       # 졸업학기(마지막 학기) 여부
    extended_enrollment: Optional[bool] = None  # 정규학기 초과 등록 여부
    graduation_deferred: Optional[bool] = None  # 졸업유예/졸업연기 여부
    tuition_paid: Optional[bool] = None         # 등록금 완납 여부
    tuition_fully_waived: Optional[bool] = None  # 등록금 전액 면제 여부

    gpa_last: Optional[float] = None            # 직전학기 평점
    gpa_cumulative: Optional[float] = None      # 전체 평점
    gpa_scale: Optional[float] = None           # 평점 만점 (4.5/4.3/4.0/5.0)
    gpa_percent: Optional[float] = None         # 직전학기 100점 환산 점수 (공고가 100점제일 때만 사용)
    gpa_cumulative_percent: Optional[float] = None   # 누적(전체) 100점 환산 점수 (공고의 전체 평점 기준이 100점제일 때만 사용)
    credits_last: Optional[float] = None        # 직전학기 취득학점
    semesters_completed: Optional[int] = None   # 등록한 학기 수
    remaining_semesters: Optional[int] = None   # 졸업까지 남은 학기 수

    residence_sido: Optional[str] = None        # 주민등록 거주 시·도
    residence_sigungu: Optional[str] = None     # 주민등록 거주 시·군·구
    residence_years: Optional[float] = None     # 현재 거주지 거주 기간(년)
    school_sido: Optional[str] = None
    school_sigungu: Optional[str] = None
    school_type: Optional[str] = None           # GENERAL/COLLEGE/REMOTE/OTHER

    income_bracket: Optional[int] = None        # 학자금 지원구간(소득분위) 0~10
    is_basic_livelihood: Optional[bool] = None  # 기초생활수급자
    is_near_poor: Optional[bool] = None         # 차상위계층
    is_single_parent: Optional[bool] = None     # 한부모가족

    nationality: Optional[str] = None           # KR / FOREIGN
    major: Optional[str] = None
    special_status: Optional[List[str]] = None  # 다문화, 장애, 가족돌봄 등 (None=미입력, []=해당 없음)
    current_scholarships: Optional[List[str]] = None
    language_scores: Optional[Dict[str, float]] = None

    # --- 프로필 v3 (항목 사전 기반, 모두 선택. 없으면 None = 알 수 없음) ---
    gpa_last_term: Optional[str] = None         # 직전학기 'YYYY-N' (성적이 오래되었는지 확인)
    term_history: Optional[List[Dict[str, Any]]] = None   # [{"term": "2026-1", "gpa": 3.79, "credits": 15.0}, ...] 최근 학기부터
    entry_year: Optional[int] = None            # 입학년도
    admission_type: Optional[str] = None        # NEW(신입학) / TRANSFER(편입학)
    gender: Optional[str] = None                # M / F
    age: Optional[int] = None                   # 만 나이
    parent_residence_sido: Optional[str] = None     # 부모 주민등록 시·도
    parent_residence_sigungu: Optional[str] = None  # 부모 주민등록 시·군·구
    hs_sido: Optional[str] = None               # 졸업 고등학교 소재지 시·도
    hs_sigungu: Optional[str] = None
    degree_held: Optional[bool] = None          # 학위 소지 여부
    national_scholarship_applied: Optional[bool] = None   # 이번 학기 국가장학금 신청 여부
    special_codes: Optional[List[str]] = None   # 해당하는 신분 표준 코드 (schema_v3.SPECIAL_CODES)
    special_complete: Optional[bool] = None     # True=신분 항목에 모두 답함(목록에 없으면 해당 없음), False=일부만 답함, None=미입력
    school_programs: Optional[List[str]] = None     # 학교 참여 사업 (학교 고정 속성)
    extra: Optional[Dict[str, Any]] = None      # 항목 사전에는 있지만 전용 필드가 없는 항목 {코드: 값}
    major_group: Optional[str] = None           # 학과가 속한 계열 표준 코드 (schema_v3.MAJOR_GROUPS). 학교 시스템에 분류된 값을 그대로 전달
    residence_years_upper: Optional[float] = None   # 거주 기간이 구간('1~3년')으로 입력된 경우의 상한(포함). 없으면 residence_years 가 정확한 값
    residence_years_open: Optional[bool] = None     # True 면 상한 없음('3년 이상'): residence_years 이상이라는 뜻


@dataclass
class BuiltProfile:
    profile: StudentProfile
    dropped_sensitive: List[str] = field(default_factory=list)    # 받지 않고 버린 개인정보 항목 이름
    ignored: List[str] = field(default_factory=list)              # 모르는 항목
    assumptions: List[str] = field(default_factory=list)          # 입력이 없어 가정한 값 (예: 만점 4.5)


def _bool(v, name: str) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1", "예", "네", "해당", "o"):
        return True
    if s in ("n", "no", "false", "0", "아니오", "아니요", "해당없음", "해당 없음", "x"):
        return False
    raise ProfileError("{}: true/false 로 입력하세요 ({!r})".format(name, v))


def _num(v, name: str, lo: float, hi: float) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise ProfileError("{}: 숫자로 입력하세요".format(name))
    try:
        x = float(str(v).replace(",", ""))
    except ValueError:
        raise ProfileError("{}: 숫자로 입력하세요 ({!r})".format(name, v))
    if not (lo <= x <= hi):
        raise ProfileError("{}: {} 범위를 벗어남 ({})".format(name, "{}~{}".format(lo, hi), x))
    return x


def _int(v, name: str, lo: int, hi: int) -> Optional[int]:
    x = _num(v, name, lo, hi)
    return None if x is None else int(x)


def _text(v) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    return s or None


def _text_max(v, name: str, maxlen: int) -> Optional[str]:
    s = _text(v)
    if s is not None and len(s) > maxlen:
        raise ProfileError("{}: {}자를 넘을 수 없습니다 ({}자)".format(name, maxlen, len(s)))
    return s


def _sido(v, name: str) -> Optional[str]:
    """시·도는 17개 공식 이름(약칭·옛 이름은 공식 이름으로 바꿔 저장)만 받는다. 오타 같은 알 수 없는 값은 거절해 조용히 미달 처리되지 않게 한다."""
    s = _text(v)
    if s is None:
        return None
    canon = vocab.canon_sido(s)
    if canon is None:
        raise ProfileError("{}: 알 수 없는 시·도입니다 ({!r}). 공식 이름(예: 경기도, 서울특별시)으로 보내 주세요".format(name, s))
    return canon


_NATION_KR = ("kr", "kor", "korea", "korean", "southkorea", "republicofkorea", "대한민국", "한국", "한국인", "내국인")
_NATION_FOREIGN = ("foreign", "foreigner", "외국인", "외국", "비내국인")
_NATION_OVERSEAS = ("overseas_kr", "overseaskorean", "재외동포", "재외국민")


def _nationality(v) -> Optional[str]:
    """KR / FOREIGN / OVERSEAS_KR(재외동포). 알 수 없는 값은 거절한다 (조용히 내국인 대상 공고에서 탈락시키지 않는다)."""
    s = _text(v)
    if s is None:
        return None
    low = re.sub(r"\(.*?\)", "", s).replace(" ", "").lower()                          # '외국인(유학생)' -> '외국인'
    if low in _NATION_KR:
        return "KR"
    if low in _NATION_FOREIGN:
        return "FOREIGN"
    if low in _NATION_OVERSEAS:
        return "OVERSEAS_KR"
    raise ProfileError("nationality: 알 수 없는 값입니다 ({!r}). KR(내국인), FOREIGN(외국인), OVERSEAS_KR(재외동포) 중 하나로 보내 주세요".format(s))


def _mapped(v, table: Dict[str, str], valid: Tuple[str, ...], name: str) -> Optional[str]:
    s = _text(v)
    if s is None:
        return None
    if s.upper() in valid:
        return s.upper()
    key = s.replace(" ", "")
    if key in table:
        return table[key]
    raise ProfileError("{}: 알 수 없는 값 {!r} (사용 가능: {})".format(name, v, ", ".join(valid)))


def _strlist(v, name: str) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        raise ProfileError("{}: 목록으로 입력하세요".format(name))
    return [s for s in (_text(x) for x in v) if s]


def _term(v, name: str) -> Optional[str]:
    if v is None or v == "":
        return None
    t = normalize_term(v)
    if t is None:
        raise ProfileError("{}: 'YYYY-N' 형식이어야 합니다 (예: 2026-1, {!r})".format(name, v))
    return t


def _history(v, name: str, max_gpa: float) -> Optional[List[Dict[str, Any]]]:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)):
        raise ProfileError("{}: 목록으로 입력하세요".format(name))
    if len(v) > 20:
        raise ProfileError("{}: 학기는 최대 20개까지 입력할 수 있습니다 ({}개)".format(name, len(v)))
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, it in enumerate(v):
        pre = "{}[{}]".format(name, i)
        if not isinstance(it, dict):
            raise ProfileError("{}: {{term, gpa, credits}} 형태로 입력하세요".format(pre))
        term = _term(it.get("term"), pre + ".term")
        if term is None:
            raise ProfileError("{}.term: 학기가 필요합니다".format(pre))
        if term in seen:
            raise ProfileError("{}: {} 학기가 두 번 들어 있습니다".format(name, term))
        seen.add(term)
        gpa = _num(it.get("gpa"), pre + ".gpa", 0, max_gpa)
        if gpa is None:
            raise ProfileError("{}.gpa: 평점이 필요합니다".format(pre))
        out.append({"term": term, "gpa": gpa, "credits": _num(it.get("credits"), pre + ".credits", 0, 60)})
    return sorted(out, key=lambda r: r["term"], reverse=True)


def _code_list(v, valid: Tuple[str, ...], table: Dict[str, str], name: str) -> Optional[List[str]]:
    items = _strlist(v, name)
    if items is None:
        return None
    out: List[str] = []
    for s in items:
        c = s.upper() if s.upper() in valid else table.get(s.replace(" ", ""))
        if c is None:
            raise ProfileError("{}: 알 수 없는 값 {!r} (사용 가능: {})".format(name, s, ", ".join(valid)))
        if c not in out:
            out.append(c)
    return out


def _extra(v) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ProfileError("extra: {코드: 값} 형태로 입력하세요")
    out: Dict[str, Any] = {}
    for k, x in v.items():
        ok = (isinstance(x, (bool, int, float, str))
              or (isinstance(x, list) and all(isinstance(i, str) for i in x))
              or (isinstance(x, dict) and all(isinstance(i, (str, type(None))) for i in x.values())))
        if not ok:
            raise ProfileError("extra.{}: 문자·숫자·참/거짓·문자 목록·지명({{sido, sigungu}}) 중 하나여야 합니다".format(k))
        out[str(k).strip().upper()] = x
    return out


def build_profile(data: Dict[str, Any], default_gpa_scale: Optional[float] = 4.5) -> BuiltProfile:
    """딕셔너리(JSON)에서 프로필을 만든다. 잘못된 값은 ProfileError."""
    if not isinstance(data, dict):
        raise ProfileError("프로필은 JSON 객체여야 합니다")
    known = {f.name for f in fields(StudentProfile)}
    dropped = sorted(k for k in data if is_sensitive_key(k))
    ignored = sorted(k for k in data if k not in known and k not in dropped)
    d = {k: v for k, v in data.items() if k in known}
    out = BuiltProfile(StudentProfile(), dropped_sensitive=dropped, ignored=ignored)
    p = out.profile

    p.level = _mapped(d.get("level"), _LEVEL_WORDS, LEVELS, "level")
    p.grade = _int(d.get("grade"), "grade", 1, 6)
    p.status = _mapped(d.get("status"), _STATUS_WORDS, STATUSES, "status")
    for k in ("final_semester", "extended_enrollment", "graduation_deferred", "tuition_paid", "tuition_fully_waived",
              "is_basic_livelihood", "is_near_poor", "is_single_parent"):
        setattr(p, k, _bool(d.get(k), k))

    p.gpa_scale = _num(d.get("gpa_scale"), "gpa_scale", 1, 5)
    if p.gpa_scale is not None and p.gpa_scale not in KNOWN_SCALES:
        raise ProfileError("gpa_scale: {} 중 하나여야 합니다 ({})".format(KNOWN_SCALES, p.gpa_scale))
    scale_for_range = p.gpa_scale or default_gpa_scale or 5.0
    p.gpa_last = _num(d.get("gpa_last"), "gpa_last", 0, scale_for_range)
    p.gpa_cumulative = _num(d.get("gpa_cumulative"), "gpa_cumulative", 0, scale_for_range)
    p.gpa_percent = _num(d.get("gpa_percent"), "gpa_percent", 0, 100)
    p.gpa_cumulative_percent = _num(d.get("gpa_cumulative_percent"), "gpa_cumulative_percent", 0, 100)
    if (p.gpa_last is not None or p.gpa_cumulative is not None) and p.gpa_scale is None:
        if default_gpa_scale:
            p.gpa_scale = default_gpa_scale
            out.assumptions.append("평점 만점 단위가 없어 {:g} 으로 가정함 (gpa_scale 로 지정 가능)".format(default_gpa_scale))
        else:
            raise ProfileError("gpa_scale: 평점을 입력할 때는 만점 단위(4.5/4.3/4.0/5.0)가 필요합니다")
    p.credits_last = _num(d.get("credits_last"), "credits_last", 0, 60)
    p.semesters_completed = _int(d.get("semesters_completed"), "semesters_completed", 0, 30)
    p.remaining_semesters = _int(d.get("remaining_semesters"), "remaining_semesters", 0, 30)

    p.residence_sido = _sido(d.get("residence_sido"), "residence_sido")
    p.residence_sigungu = _text_max(d.get("residence_sigungu"), "residence_sigungu", 40)
    p.residence_years = _num(d.get("residence_years"), "residence_years", 0, 100)
    p.school_sido = _text(d.get("school_sido"))
    p.school_sigungu = _text(d.get("school_sigungu"))
    p.school_type = _mapped(d.get("school_type"), _SCHOOL_WORDS, SCHOOL_TYPES, "school_type")

    p.income_bracket = _int(d.get("income_bracket"), "income_bracket", 0, 10)
    p.nationality = _nationality(d.get("nationality"))
    p.major = _text_max(d.get("major"), "major", 100)
    p.special_status = _strlist(d.get("special_status"), "special_status")
    p.current_scholarships = _strlist(d.get("current_scholarships"), "current_scholarships")
    ls = d.get("language_scores")
    if ls is not None:
        if not isinstance(ls, dict):
            raise ProfileError("language_scores: {시험명: 점수} 형태로 입력하세요")
        p.language_scores = {str(k).strip().upper(): _num(v, "language_scores." + str(k), 0, 1000) for k, v in ls.items()}

    # --- 프로필 v3 ---
    p.gpa_last_term = _term(d.get("gpa_last_term"), "gpa_last_term")
    p.term_history = _history(d.get("term_history"), "term_history", scale_for_range)
    p.entry_year = _int(d.get("entry_year"), "entry_year", 1990, 2100)
    p.admission_type = _mapped(d.get("admission_type"), _ADMISSION_WORDS, ADMISSION_TYPES, "admission_type")
    p.gender = _mapped(d.get("gender"), _GENDER_WORDS, GENDERS, "gender")
    p.age = _int(d.get("age"), "age", 0, 100)
    for k in ("parent_residence_sido", "hs_sido"):
        setattr(p, k, _sido(d.get(k), k))
    for k in ("parent_residence_sigungu", "hs_sigungu"):
        setattr(p, k, _text_max(d.get(k), k, 40))
    p.degree_held = _bool(d.get("degree_held"), "degree_held")
    p.national_scholarship_applied = _bool(d.get("national_scholarship_applied"), "national_scholarship_applied")
    p.special_codes = _code_list(d.get("special_codes"), SPECIAL_CODES, {}, "special_codes")
    p.special_complete = _bool(d.get("special_complete"), "special_complete")
    p.school_programs = _code_list(d.get("school_programs"), SCHOOL_PROGRAMS, _PROGRAM_WORDS, "school_programs")
    p.extra = _extra(d.get("extra"))
    mg = d.get("major_group")
    p.major_group = _mapped(re.sub(r"계열$", "", mg.strip()) if isinstance(mg, str) else mg, _MAJOR_WORDS, MAJOR_GROUPS, "major_group")
    p.residence_years_upper = _num(d.get("residence_years_upper"), "residence_years_upper", 0, 100)
    p.residence_years_open = _bool(d.get("residence_years_open"), "residence_years_open")
    if p.residence_years_upper is not None and p.residence_years is not None and p.residence_years_upper < p.residence_years:
        raise ProfileError("residence_years_upper: 거주 기간 하한({:g})보다 작을 수 없습니다 ({:g})".format(p.residence_years, p.residence_years_upper))
    if p.residence_years_open and p.residence_years_upper is not None:
        raise ProfileError("residence_years_open 과 residence_years_upper 는 함께 쓸 수 없습니다")
    if p.term_history and p.gpa_scale is None:                    # 학기별 성적만 있어도 만점 단위가 필요하다 (평점과 같은 가정)
        if default_gpa_scale:
            p.gpa_scale = default_gpa_scale
            out.assumptions.append("평점 만점 단위가 없어 {:g} 으로 가정함 (gpa_scale 로 지정 가능)".format(default_gpa_scale))
        else:
            raise ProfileError("gpa_scale: 학기별 성적을 입력할 때는 만점 단위(4.5/4.3/4.0/5.0)가 필요합니다")
    return out
