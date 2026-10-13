# -*- coding: utf-8 -*-
"""[RECOMMEND] 판정 엔진 v3 - 4-B1: 신분, 나이, 성별, 입학년도, 편입, 학과 계열, 학교 종류, 면제 + 별도 표기.

판정(충족·조건부·미달)에 영향을 주는 것
  신입생 성적 면제, 면제(waivers), 필수 신분, 나이, 성별, 입학년도, 편입, 학과 계열, 학교 종류, 학적 제외 문구
판정에 영향을 주지 않고 따로 표시하는 것 (결과의 별도 항목)
  선행 조건(국가장학금 신청 등), 우대 조건(우대 신분), 학교 참여 사업, 확인 사항(중복수혜 제한·선발 후 의무·심사 기준·추천·절차)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import vocab
from .matcher import FAIL, PASS, UNKNOWN, Check, _combine_any, _excluded_phrase, _inv
from .profile import StudentProfile

SPECIAL_LABEL = {"BASIC_LIVELIHOOD": "기초생활수급자", "NEAR_POOR": "차상위계층", "SINGLE_PARENT": "한부모가정", "DISABLED": "장애", "DEFECTOR": "북한이탈주민",
                 "MULTICULTURAL": "다문화가정", "VETERAN": "국가유공자", "MULTI_CHILD": "다자녀가정", "WORKER_CHILD": "근로자 자녀", "OTHER": "기타 신분"}
# 학생 입력 항목(항목 사전의 special_code)이 있는 신분. 나머지(근로자 자녀·기타)는 학생에게 묻지 않으므로 코드가 판정하지 않는다.
ANSWERABLE_SPECIAL = ("BASIC_LIVELIHOOD", "NEAR_POOR", "SINGLE_PARENT", "DISABLED", "DEFECTOR", "MULTICULTURAL", "VETERAN", "MULTI_CHILD")
_SPECIAL_FLAG = {"BASIC_LIVELIHOOD": "is_basic_livelihood", "NEAR_POOR": "is_near_poor", "SINGLE_PARENT": "is_single_parent"}
MAJOR_LABEL = {"ENGINEERING": "공학", "NATURAL": "자연", "MEDICAL": "의약", "ARTS_PE": "예체능", "HUMANITIES": "인문", "SOCIAL": "사회", "EDUCATION": "교육", "AGRICULTURE": "농림"}
SCHOOL_LABEL = {"UNIV4": "4년제 대학", "COLLEGE": "전문대학", "CYBER": "사이버대학", "OPEN_UNIV": "방송통신대학", "COMPANY_UNIV": "사내대학"}
WAIVER_STUDENT_LABEL = {"NEW_STUDENT": "신입생", "TRANSFER": "편입생", "REENTRY": "재입학생", "RETURNING": "복학생"}
KEY_LABEL = {"gpa": "성적", "credits": "학점", "income": "소득", "region": "지역"}
PREREQ_LABEL = {"NATIONAL_SCHOLARSHIP_APPLIED": "국가장학금 신청", "BRACKET_CALCULATED": "학자금 지원구간 산정", "ADMISSION_OFFER": "입학허가",
                "EXCHANGE_SELECTED": "교환학생 선정", "DEGREE_HELD": "학위 소지", "OTHER": "기타 선행 조건"}
PROGRAM_LABEL = {"NATIONAL_SCHOLARSHIP": "국가장학금 참여대학", "LOCAL_TALENT": "지역인재장학금 참여대학", "HOUSING_STABILITY": "주거안정장학금 참여대학"}
NOTE_LABEL = {"EXCLUSION": "제외·중복수혜 제한", "BENEFIT_LIMIT": "혜택 제한", "PROCEDURE": "절차·서류", "OBLIGATION": "선발 후 의무", "REVIEW": "심사 기준", "RECOMMENDATION": "추천 필요"}
_FROM_LABEL = {"PROFESSOR": "지도교수", "PRESIDENT": "총장", "PRINCIPAL": "학교장", "DEAN": "학장·학과장", "AGENCY": "기관·단체장", "OTHER": "기타"}


# ================================================================== 학생 상태
def is_new_student(p: StudentProfile) -> Optional[bool]:
    """신입생 = 아직 한 학기도 이수하지 않은 학생 (편입생 제외). 직전학기 성적이 없어 성적 조건이 적용되지 않는다. 이수학기를 모르면 None."""
    if p.admission_type == "TRANSFER":
        return False
    if p.semesters_completed is None:
        return None
    return p.semesters_completed == 0


def special_status(p: StudentProfile, code: str) -> Optional[bool]:
    """신분 해당 여부: True / False / None(모름). 학생이 모든 신분 항목에 답했으면(special_complete) 목록에 없는 것은 해당 없음."""
    if code not in ANSWERABLE_SPECIAL:
        return None
    if code in (p.special_codes or []):
        return True
    flag = _SPECIAL_FLAG.get(code)
    if flag and getattr(p, flag) is not None:
        return getattr(p, flag)
    return False if p.special_complete else None


# ================================================================== 신분
def check_special_required(groups: List[Dict[str, Any]], p: StudentProfile, scope: str) -> Optional[Check]:
    """필수 신분: 나열된 신분 중 하나만 해당하면 충족 (신분은 서로 겹치지 않는 선택지로 나열된다)."""
    req = [g for g in groups or [] if isinstance(g, dict) and g.get("mode") == "REQUIRED" and g.get("code")]
    if not req:
        return None
    names = " / ".join(SPECIAL_LABEL.get(g["code"], g["code"]) for g in req)
    sts = [special_status(p, g["code"]) for g in req]
    if any(s is True for s in sts):
        hit = [g for g, s in zip(req, sts) if s is True]
        note = " [본인·가족 구분은 확인 필요]" if any(g["code"] in ("DISABLED", "VETERAN") and g.get("scope") == "SELF" for g in hit) else ""
        return Check("special", PASS, "신분 조건 해당 ({}){}".format(" / ".join(SPECIAL_LABEL.get(g["code"], g["code"]) for g in hit), note), scope)
    if all(s is False for s in sts):
        return Check("special", FAIL, "신분 조건 미해당 (필요: {} 중 하나)".format(names), scope)
    asks = ("SPECIAL",) if any(s is None and g["code"] in ANSWERABLE_SPECIAL for g, s in zip(req, sts)) else ()      # 근로자 자녀 같은 입력 항목이 없는 신분은 질문으로 풀 수 없다
    return Check("special", UNKNOWN, "신분 확인 필요 (필요: {} 중 하나)".format(names), scope, needs=asks)


def merge_special_income(sp: Check, inc: Check) -> Check:
    """신분과 소득 조건이 같은 곳에 함께 있으면 공고가 '또는'인지 '그리고'인지 불명확할 수 있다(예: '기초수급자·차상위 or 소득분위 1~4구간').
    두 해석에서 같은 결과일 때만 단정한다: 둘 다 충족이면 충족, 둘 다 미달이면 미달, 그 외는 확인 필요."""
    if sp.status == PASS and inc.status == PASS:
        st = PASS
    elif sp.status == FAIL and inc.status == FAIL:
        st = FAIL
    else:
        st = UNKNOWN
    note = " - 신분과 소득 조건의 결합('또는'/'그리고')이 공고에 불명확해 한쪽만 충족이면 확인 필요" if st == UNKNOWN and PASS in (sp.status, inc.status) else ""
    asks = tuple(dict.fromkeys(((sp.needs or ()) if sp.status == UNKNOWN else ()) + ((inc.needs or ()) if inc.status == UNKNOWN else ())))    # 실제로 모르는 쪽만 (둘 다 알고 결합만 모호하면 질문으로 풀 수 없다)
    return Check("special", st, "{} / {}{}".format(sp.message, inc.message, note), sp.scope, needs=asks)


# ================================================================== 나이·성별·입학년도·편입·학교 종류·학과 계열
def _age_text(mn: Optional[float], mx: Optional[float]) -> str:
    if mn is not None and mx is not None:
        return "만 {:g}~{:g}세".format(mn, mx)
    return "만 {:g}세 {}".format(mn if mn is not None else mx, "이상" if mn is not None else "이하")


def check_age(age: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    mn, mx = (age or {}).get("min"), (age or {}).get("max")
    if mn is None and mx is None:
        return []
    need = _age_text(mn, mx)
    if p.age is None:
        return [Check("age", UNKNOWN, "나이 정보 없음 (기준: {})".format(need), scope)]
    ok = (mn is None or p.age >= mn) and (mx is None or p.age <= mx)
    return [Check("age", PASS if ok else FAIL, "만 {}세 ({} {})".format(p.age, need, "충족" if ok else "미달"), scope)]


def check_gender(gender: Optional[str], p: StudentProfile, scope: str) -> List[Check]:
    if gender not in ("M", "F"):
        return []
    need = "남학생" if gender == "M" else "여학생"
    if p.gender is None:
        return [Check("gender", UNKNOWN, "성별 정보 없음 (공고 대상: {})".format(need), scope)]
    ok = p.gender == gender
    return [Check("gender", PASS if ok else FAIL, "성별 {} (공고 대상: {})".format("해당" if ok else "대상 아님", need), scope)]


def check_entry_years(years: List[int], p: StudentProfile, scope: str) -> List[Check]:
    if not years:
        return []
    need = ", ".join(str(y) for y in years)
    if p.entry_year is None:
        return [Check("entry", UNKNOWN, "입학년도 정보 없음 (공고 대상: {})".format(need), scope)]
    ok = p.entry_year in years
    return [Check("entry", PASS if ok else FAIL, "{}년 입학 ({}: {})".format(p.entry_year, "대상" if ok else "대상 아님", need), scope)]


def check_transfer(tr: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    if (tr or {}).get("mode") != "EXCLUDED":
        return []                                                       # 나머지 모드(성적 면제·전적대 성적)는 면제 판정에서 처리
    if p.admission_type is None:
        return [Check("transfer", UNKNOWN, "입학구분 정보 없음 (편입생 제외 공고)", scope)]
    if p.admission_type == "TRANSFER":
        return [Check("transfer", FAIL, "편입생 제외 공고 (학생: 편입학)", scope)]
    return [Check("transfer", PASS, "편입생 제외 공고 (학생: 신입학)", scope)]


def check_school_types(school: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    """학교 종류는 서버 설정(고정 속성)이라 판정에 쓴다. 참여 사업은 별도 표기.
    types(4년제·전문대)는 본교 종류와 직접 비교한다. excluded_types(사이버대·방송통신대·사내대학)는 일반대학·전문대학이 아니라는 뜻이라 해당하지 않으며,
    원격대학(사이버대 또는 방송통신대)은 둘 중 어느 쪽인지 알 수 없어 두 가지가 모두 제외일 때만 미달로 본다."""
    types, excluded = (school or {}).get("types") or [], (school or {}).get("excluded_types") or []
    if not types and not excluded:
        return []
    need = ", ".join(([("대상: " + " / ".join(SCHOOL_LABEL.get(t, t) for t in types))] if types else [])
                     + ([("제외: " + " / ".join(SCHOOL_LABEL.get(t, t) for t in excluded))] if excluded else []))
    if p.school_type is None:
        return [Check("school", UNKNOWN, "학교 종류 설정이 없음 (.env 의 RECOMMEND_SCHOOL_TYPE) (공고 대상: {})".format(need), scope)]
    sts: List[str] = []
    if types:
        mine = {"GENERAL": "UNIV4", "COLLEGE": "COLLEGE"}.get(p.school_type)
        sts.append(UNKNOWN if mine is None else (PASS if mine in types else FAIL))
    if excluded:
        if p.school_type in ("GENERAL", "COLLEGE"):
            sts.append(PASS)
        elif p.school_type == "REMOTE":
            remote = {"CYBER", "OPEN_UNIV"} & set(excluded)
            sts.append(FAIL if len(remote) == 2 else (UNKNOWN if remote else PASS))
        else:
            sts.append(UNKNOWN)
    st = FAIL if FAIL in sts else (PASS if all(s == PASS for s in sts) else UNKNOWN)
    msg = {PASS: "학교 종류 해당 (본교: {}; 공고 {})", FAIL: "학교 종류가 대상이 아님 (본교: {}; 공고 {})",
           UNKNOWN: "학교 종류({}) 해당 여부 확인 필요 (공고 {})"}[st]
    mine_label = {"GENERAL": "일반대학", "COLLEGE": "전문대학", "REMOTE": "원격대학", "OTHER": "기타"}.get(p.school_type, p.school_type)
    return [Check("school", st, msg.format(mine_label, need), scope)]


def check_major_groups(groups: List[str], p: StudentProfile, scope: str) -> List[Check]:
    if not groups:
        return []
    need = " / ".join(MAJOR_LABEL.get(g, g) for g in groups)
    if p.major_group is None:
        return [Check("major_group", UNKNOWN, "학과 계열 정보 없음 (공고 대상: {})".format(need), scope)]
    ok = p.major_group in groups
    return [Check("major_group", PASS if ok else FAIL, "학과 계열 {} (공고 대상: {})".format(MAJOR_LABEL.get(p.major_group, p.major_group) + (" 해당" if ok else " 대상 아님"), need), scope)]


def check_new_student_only(flag: Any, p: StudentProfile, scope: str) -> List[Check]:
    """'신입생만 대상' (공고의 new_student_only). 신입생 = 한 학기도 이수하지 않은 학생, 편입생은 신입생이 아니다."""
    if flag is not True:
        return []
    ns = is_new_student(p)
    if ns is None:
        return [Check("new_student", UNKNOWN, "이수학기 정보 없음 (신입생만 대상)", scope)]
    if ns:
        return [Check("new_student", PASS, "신입생 대상 (이수 0학기)", scope)]
    why = "편입생" if p.admission_type == "TRANSFER" else "이수 {}학기".format(p.semesters_completed)
    return [Check("new_student", FAIL, "신입생만 대상 ({})".format(why), scope)]


# ------------------------------------------------------------------ 어학 요건
EXAM_ALIAS = {"토익": "TOEIC", "토플": "TOEFL", "TOEFLIBT": "TOEFL", "텝스": "TEPS", "오픽": "OPIC", "아이엘츠": "IELTS", "한어수평고시": "HSK", "일본어능력시험": "JLPT", "토픽": "TOPIK"}
LOWER_IS_BETTER = ("JLPT",)                                                   # N1 이 N5 보다 높은 등급이다
LEVEL_EXAMS = ("HSK", "TOPIK", "JLPT")                                        # 등급이 숫자인 시험만 등급을 비교한다 (OPIc 'IM2' 같은 문자 등급에서 숫자를 뽑아 비교하면 안 된다)


def _exam_key(name: Any) -> str:
    k = re.sub(r"[\s()\[\]·.\-]+", "", str(name or "")).upper()
    return EXAM_ALIAS.get(k, k)


def _first_number(text: Any) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


CEFR_RANK = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}           # 유럽언어기준(CEFR) 등급은 1~6 숫자로 입력받는다 (독일어 B1 -> 3)
_CEFR_WORD = re.compile(r"\b([ABC][12])\b", re.I)


def cefr_rank(text: Any) -> Optional[int]:
    """'B1', 'B1 이상' 같은 문구에서 CEFR 등급 숫자를 뽑는다 (없으면 None)."""
    m = _CEFR_WORD.search(str(text or ""))
    return CEFR_RANK[m.group(1).upper()] if m else None


def _cefr_language(exam: Any) -> str:
    """'독일어(유럽언어기준 CEFR)' -> '독일어'. 언어 이름만 남긴다 (알 수 없으면 빈 문자열)."""
    head = re.split(r"[(（\[]", str(exam or ""), maxsplit=1)[0]
    head = re.sub(r"유럽\s*언어\s*(?:공통\s*)?기준|CEFR?|\s+", "", head, flags=re.I)
    return head.upper()


def _one_language(r: Dict[str, Any], p: StudentProfile) -> Tuple[str, str]:
    exam = _exam_key(r.get("exam"))
    need_score, need_level = r.get("min_score"), r.get("level")
    level_text = re.sub(r"\s*이상\s*$", "", str(need_level or "")).strip()         # '5급 이상' 에 '이상' 이 또 붙지 않게
    label = "{} {}".format(r.get("exam"), "{:g}점".format(need_score) if need_score is not None else level_text).strip() + " 이상"
    need_cefr = cefr_rank(need_level) if need_score is None else None
    if p.language_scores is None:
        return UNKNOWN, "어학 성적 정보 없음 (필요: {})".format(label)
    if need_cefr is not None:                                                     # 독일어 B1 이상 같은 유럽언어기준 등급
        lang = _cefr_language(r.get("exam"))
        if not lang:
            return UNKNOWN, "어떤 언어의 등급인지 알 수 없어 확인 필요 (필요: {})".format(label)
        have = {_cefr_language(k): v for k, v in p.language_scores.items()}.get(lang)
        if have is None:
            return FAIL, "{} 성적이 없음 (필요: {})".format(r.get("exam"), label)
        ok = have >= need_cefr
        got = {v: k for k, v in CEFR_RANK.items()}.get(int(have), "{:g}".format(have))
        return (PASS if ok else FAIL), "{} {} (필요: {})".format(r.get("exam"), got, label)
    have = {_exam_key(k): v for k, v in p.language_scores.items()}.get(exam)
    if have is None:
        return FAIL, "{} 성적이 없음 (필요: {})".format(r.get("exam"), label)
    if need_score is not None:
        ok = have >= need_score
        return (PASS if ok else FAIL), "{} {:g} (필요: {})".format(r.get("exam"), have, label)
    need = _first_number(need_level) if exam in LEVEL_EXAMS else None
    if need is None:
        return UNKNOWN, "{} 기준 등급을 해석하지 못해 확인 필요 (필요: {})".format(r.get("exam"), label)
    ok = have <= need if exam in LOWER_IS_BETTER else have >= need
    return (PASS if ok else FAIL), "{} {:g} (필요: {})".format(r.get("exam"), have, label)


def check_language(reqs: List[Dict[str, Any]], p: StudentProfile, scope: str) -> List[Check]:
    """어학 요건: 여러 시험이 나열되면 하나만 충족해도 된다 ('TOEIC 700 또는 TOEFL 80'). 성적을 입력했는데 그 시험이 없으면 미충족, 입력하지 않았으면 확인 필요."""
    rows = [_one_language(r, p) for r in reqs or [] if isinstance(r, dict) and r.get("exam")]
    if not rows:
        return []
    sts = [s for s, _ in rows]
    st = PASS if PASS in sts else (FAIL if all(s == FAIL for s in sts) else UNKNOWN)
    pick = [m for s, m in rows if s == st] or [m for _, m in rows]
    head = {PASS: "어학 요건 충족", FAIL: "어학 요건 미달", UNKNOWN: "어학 요건 확인 필요"}[st]
    return [Check("language", st, "{} ({})".format(head, " / ".join(pick)), scope)]


def check_extras(d: Dict[str, Any], p: StudentProfile, scope: str, top: bool, check_nationality) -> List[Check]:
    out: List[Check] = []
    out += check_new_student_only(d.get("new_student_only"), p, scope)
    out += check_language(d.get("language_requirements") or [], p, scope)
    out += check_age(d.get("age") or {}, p, scope)
    out += check_gender(d.get("gender"), p, scope)
    out += check_entry_years(d.get("entry_years") or [], p, scope)
    out += check_transfer(d.get("transfer") or {}, p, scope)
    out += check_major_groups(d.get("major_groups") or [], p, scope)
    if top:
        out += check_school_types(d.get("school") or {}, p, scope)
    else:
        out += check_nationality(d.get("nationality"), p, scope)         # 최상위 국적은 v2 판정이 따로 한다
    return out


# ================================================================== 제외 조건 중 학적·등록 상태 문구 (판정) / 나머지 (별도 표기)
def check_exclusions_v3(texts: List[str], p: StudentProfile, scope: str) -> Tuple[List[Check], List[str]]:
    checks: List[Check] = []
    rest: List[str] = []
    for ex in texts or []:
        tags = vocab.status_tags(ex) - {vocab.ENROLLED}
        if tags:
            checks += _excluded_phrase(ex, p, scope, "exclusion")
        elif re.search(r"등록금", ex) and re.search(r"면제|자부담금이?\s*없", ex):
            checks.append(Check("exclusion", _inv(p.tuition_fully_waived), "등록금 전액 면제자 제외", scope))
        elif len(ex.strip()) <= 12 and re.search(r"자퇴|퇴학|제적", ex) and not re.search(r"휴학|졸업", ex):
            # '자퇴' 같은 짧은 제외 문구: 지금 재학 중인 학생은 해당하지 않는다 (학적 상태를 모르면 확인 필요)
            checks.append(Check("exclusion", UNKNOWN if p.status is None else (PASS if p.status == "ENROLLED" else UNKNOWN), "자퇴·퇴학·제적자 제외", scope))
        else:
            rest.append(ex)
    return checks, rest


# ================================================================== 면제(waivers)
def _waiver_student_status(code: str, p: StudentProfile) -> Optional[bool]:
    if code == "NEW_STUDENT":
        return is_new_student(p)
    if code == "TRANSFER":
        return None if p.admission_type is None else p.admission_type == "TRANSFER"
    return None                                                          # 재입학·복학: 입력 항목이 없어 알 수 없다


def waiver_applies(w: Dict[str, Any], p: StudentProfile) -> Optional[bool]:
    """면제 대상인가: 조건(학생 유형, 신분) 중 하나라도 해당하면 True, 모두 해당하지 않으면 False, 그 외 None."""
    when = w.get("when") or {}
    sts = [_waiver_student_status(s, p) for s in when.get("student") or []] + [special_status(p, c) for c in when.get("special") or []]
    if not sts:
        return None
    if any(s is True for s in sts):
        return True
    return False if all(s is False for s in sts) else None


def _waiver_reason(w: Dict[str, Any]) -> str:
    when = w.get("when") or {}
    return "·".join([WAIVER_STUDENT_LABEL.get(s, s) for s in when.get("student") or []] + [SPECIAL_LABEL.get(c, c) for c in when.get("special") or []])


def waiver_effects(elig: Dict[str, Any], p: StudentProfile) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(면제되는 항목 -> 이유, 면제일 수 있는 항목 -> 이유). 면제 문구 + 편입 모드(성적 요건 없음 / 전적대 성적)."""
    waived: Dict[str, str] = {}
    maybe: Dict[str, str] = {}
    for w in elig.get("waivers") or []:
        ap = waiver_applies(w, p)
        for key in w.get("skip") or []:
            if ap is True:
                waived.setdefault(key, _waiver_reason(w))
            elif ap is None:
                maybe.setdefault(key, _waiver_reason(w))
    mode = (elig.get("transfer") or {}).get("mode")
    if mode in ("NO_GPA_REQUIRED", "USE_PREV_SCHOOL"):
        is_tr = None if p.admission_type is None else p.admission_type == "TRANSFER"
        for key in ("gpa", "credits"):
            if mode == "NO_GPA_REQUIRED" and is_tr is True:
                waived.setdefault(key, "편입생")
            elif is_tr is None or (mode == "USE_PREV_SCHOOL" and is_tr is True):
                maybe.setdefault(key, "편입생")                         # 전적대 성적은 입력받지 않는다
    for key in list(maybe):
        if key in waived:
            del maybe[key]
    return waived, maybe


def apply_waivers(checks: List[Check], waived: Dict[str, str], maybe: Dict[str, str]) -> List[Check]:
    """면제 대상 항목은 충족으로 바꾸고(면제 이유 표시), 면제일 수 있는 항목의 미달은 탈락시키지 않고 확인 필요로 낮춘다."""
    if not waived and not maybe:
        return checks
    out: List[Check] = []
    for c in checks:
        if c.key in waived and c.status != PASS:
            out.append(Check(c.key, PASS, "{} 조건 면제 ({})".format(KEY_LABEL.get(c.key, c.key), waived[c.key]), c.scope))
        elif c.key in maybe and c.status == FAIL:
            out.append(Check(c.key, UNKNOWN, "{} [면제 대상일 수 있음: {} - 확인 필요]".format(c.message, maybe[c.key]), c.scope))
        else:
            out.append(c)
    return out


# ================================================================== 별도 표기 (판정에 영향 없음)
def build_prerequisites(elig: Dict[str, Any], p: StudentProfile) -> List[Dict[str, Any]]:
    """선행 조건: status = MET(충족) / TODO(신청 필요) / UNMET(미충족) / UNKNOWN(확인 필요)"""
    out: List[Dict[str, Any]] = []
    for it in elig.get("prerequisites") or []:
        t = it.get("type") or "OTHER"
        if t == "NATIONAL_SCHOLARSHIP_APPLIED":
            st = {True: "MET", False: "TODO", None: "UNKNOWN"}[p.national_scholarship_applied]
        elif t == "BRACKET_CALCULATED":
            st = "MET" if p.income_bracket is not None else "UNKNOWN"
        elif t == "DEGREE_HELD":
            st = {True: "MET", False: "UNMET", None: "UNKNOWN"}[p.degree_held]
        else:
            st = "UNKNOWN"
        out.append({"type": t, "label": PREREQ_LABEL.get(t, t), "text": it.get("text"), "status": st})
    return out


def build_preferences(d: Dict[str, Any], p: StudentProfile) -> List[Dict[str, Any]]:
    """우대 조건: 우대 신분. applies = True(해당) / False(해당 없음) / None(모름)"""
    return [{"code": g["code"], "label": SPECIAL_LABEL.get(g["code"], g["code"]), "applies": special_status(p, g["code"])}
            for g in d.get("special_groups") or [] if isinstance(g, dict) and g.get("mode") == "PREFERRED" and g.get("code")]


def build_school_programs(elig: Dict[str, Any], p: StudentProfile) -> List[Dict[str, Any]]:
    """학교 참여 사업: school = PARTICIPATES(본교 참여) / NOT(본교 미참여) / UNKNOWN(설정 없음)"""
    out = []
    for code in (elig.get("school") or {}).get("programs") or []:
        school = "UNKNOWN" if p.school_programs is None else ("PARTICIPATES" if code in p.school_programs else "NOT")
        out.append({"program": code, "label": PROGRAM_LABEL.get(code, code), "school": school})
    return out


def build_notes(elig: Dict[str, Any], exclusion_rest: List[str]) -> List[Dict[str, str]]:
    """확인 사항: 중복수혜 제한, 혜택 제한, 절차·서류, 선발 후 의무, 심사 기준, 추천. 자격 문장(ELIGIBILITY)은 판정에서 확인 항목으로 다룬다."""
    out: List[Dict[str, str]] = []
    seen = set()

    def add(kind: str, text: Optional[str]) -> None:
        text = (text or "").strip()
        key = re.sub(r"\s+", "", text)
        if text and key not in seen:
            seen.add(key)
            out.append({"kind": kind, "label": NOTE_LABEL[kind], "text": text})

    for t in exclusion_rest:
        add("EXCLUSION", t)
    for u in elig.get("unmodeled_conditions") or []:
        if isinstance(u, dict) and u.get("kind") in NOTE_LABEL and u.get("kind") != "ELIGIBILITY":
            add(u["kind"], u.get("text"))
    for t in elig.get("obligations") or []:
        add("OBLIGATION", t)
    for t in elig.get("review_criteria") or []:
        add("REVIEW", t)
    for t in elig.get("documents") or []:
        add("PROCEDURE", t)
    rec = elig.get("recommendation") or {}
    if rec.get("required") or rec.get("from"):
        who = "·".join(_FROM_LABEL.get(x, x) for x in rec.get("from") or [])
        add("RECOMMENDATION", "추천서 필요" + (" ({})".format(who) if who else ""))
    order = list(NOTE_LABEL)                                              # 표시 순서는 항상 같다 (AI 가 적은 순서와 무관)
    out.sort(key=lambda x: order.index(x["kind"]))
    return out
