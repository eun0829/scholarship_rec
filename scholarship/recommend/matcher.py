# -*- coding: utf-8 -*-
"""[RECOMMEND] 공고(ELIG_JSON)와 학생 프로필 비교.

각 조건은 PASS(충족) / FAIL(미달) / UNKNOWN(학생 정보가 없거나 코드가 판정할 수 없음) 중 하나다.
  - 학생 정보가 없다고 탈락시키지 않는다 (UNKNOWN -> '조건부').
  - 문장으로만 적힌 조건(other_conditions 등)은 코드가 해석하지 않고 '확인 항목'(UNKNOWN)으로 돌려준다.

variants 해석 (스키마 v2)
  최상위 = 모든 유형 공통 조건 / variants[i] = 공통에 '추가로' 요구되는 조건 (null·빈 값 = 추가 조건 없음)
  1) 공통 조건에 FAIL 이 하나라도 있으면 전체 미달
  2) variants 가 없으면 공통 결과가 곧 결과
  3) 있으면 유형마다 (공통 + 그 유형)을 판정하고 어느 한 유형이라도 FAIL 이 아니면 후보
구형식(schema_version < 2) 결과는 유형별 조건이 합쳐져 있을 수 있어, 그런 징후가 있는 항목(gpa/credits/income)의
FAIL 은 UNKNOWN 으로 낮춘다 (거짓 탈락 방지).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import vocab
from . import needs as needs_mod
from .profile import StudentProfile

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
MATCH, CONDITIONAL, NO_MATCH = "MATCH", "CONDITIONAL", "NO_MATCH"
SCALE_MARGIN = 0.02            # 만점이 다른 평점 환산 시 경계 허용 비율 (경계 안쪽은 UNKNOWN)


@dataclass
class Check:
    key: str                     # levels / grade / enrollment / gpa / credits / income / region / nationality / major / special / other / exclusion
    status: str
    message: str
    scope: str = "공통"          # '공통' 또는 유형 이름
    needs: Optional[tuple] = None    # 확인 필요일 때 풀어 줄 수 있는 학생 입력 항목 후보(항목 사전 코드). None=아직 표시 안 함, ()=입력으로는 풀 수 없다고 표시함 (5-A)


@dataclass
class VariantResult:
    name: str
    verdict: str
    amount: Optional[str]
    checks: List[Check] = field(default_factory=list)
    apply_start: Optional[str] = None                        # 유형별 신청 시작·마감 (공고가 유형마다 다르게 적은 경우)
    apply_end: Optional[str] = None
    preferences: List[Dict[str, Any]] = field(default_factory=list)    # 이 유형의 우대 조건 (v3)

    @property
    def to_confirm(self) -> List[str]:
        return [c.message for c in self.checks if c.status == UNKNOWN]

    @property
    def fails(self) -> List[str]:
        return [c.message for c in self.checks if c.status == FAIL]


@dataclass
class Evaluation:
    verdict: str
    common: List[Check] = field(default_factory=list)
    variants: List[VariantResult] = field(default_factory=list)
    fail_reasons: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)    # 판정에 쓴 가정 (예: 공고에 만점이 없어 4.5 로 가정). v3 판정이 채운다
    # 판정(충족·조건부·미달)에 영향을 주지 않고 따로 표시하는 항목 (v3 판정이 채운다)
    prerequisites: List[Dict[str, Any]] = field(default_factory=list)   # 선행 조건: 국가장학금 신청 등
    preferences: List[Dict[str, Any]] = field(default_factory=list)     # 우대 조건: 우대 신분
    school_programs: List[Dict[str, Any]] = field(default_factory=list)  # 학교 참여 사업과 본교 참여 여부
    notes: List[Dict[str, str]] = field(default_factory=list)           # 확인 사항: 중복수혜 제한, 선발 후 의무, 심사 기준, 추천, 절차

    @property
    def to_confirm(self) -> List[str]:
        return [c.message for c in self.common if c.status == UNKNOWN]

    @property
    def passed(self) -> List[str]:
        return [c.message for c in self.common if c.status == PASS]

    @property
    def matched_variants(self) -> List[VariantResult]:
        order = {MATCH: 0, CONDITIONAL: 1}
        return sorted((v for v in self.variants if v.verdict != NO_MATCH), key=lambda v: order[v.verdict])


# ================================================================== 작은 도우미
def _bool_status(v: Optional[bool]) -> str:
    return UNKNOWN if v is None else (PASS if v else FAIL)


def _combine_any(statuses: List[str]) -> str:
    """선택지(OR) 결합: 하나라도 PASS 면 PASS, 전부 FAIL 이면 FAIL, 그 외 UNKNOWN."""
    if not statuses:
        return PASS
    if PASS in statuses:
        return PASS
    if all(s == FAIL for s in statuses):
        return FAIL
    return UNKNOWN


def _norm_words(text: str) -> str:
    return re.sub(r"[\s\(\)\[\]·,/]", "", (text or "")).lower()


def _text_matches_any(text: str, words: Optional[List[str]]) -> Optional[bool]:
    """공고 문구가 학생이 입력한 항목과 겹치는지. 학생 입력이 없으면 None."""
    if words is None:
        return None
    t = _norm_words(text)
    return any(w and (_norm_words(w) in t or t in _norm_words(w)) for w in words)


# ================================================================== 조건별 판정
def check_levels(levels: List[str], p: StudentProfile, scope: str) -> List[Check]:
    if not levels:
        return []
    if p.level is None:
        return [Check("levels", UNKNOWN, "학교급 정보 없음 (공고 대상: {})".format(", ".join(levels)), scope)]
    if p.level in levels:
        return [Check("levels", PASS, "대상 학교급 해당 ({})".format(p.level), scope)]
    return [Check("levels", FAIL, "대상 학교급이 아님 (공고 대상: {}, 학생: {})".format(", ".join(levels), p.level), scope)]


def check_grades(grades: List[int], p: StudentProfile, scope: str) -> List[Check]:
    if not grades:
        return []
    if p.grade is None:
        return [Check("grade", UNKNOWN, "학년 정보 없음 (공고 대상 학년: {})".format(grades), scope)]
    ok = p.grade in grades
    return [Check("grade", PASS if ok else FAIL, "{}학년 {} (공고 대상 학년: {})".format(p.grade, "해당" if ok else "대상 아님", grades), scope)]


def check_enrollment(en: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    out: List[Check] = []
    # ---- 필수 상태
    for phrase in en.get("required_status") or []:
        tags = vocab.status_tags(phrase)
        sub: List[Check] = []
        if vocab.ENROLLED in tags:
            if p.status is None:
                sub.append(Check("enrollment", UNKNOWN, "학적 상태 정보 없음 ('{}' 필요)".format(phrase), scope))
            elif p.status == "ENROLLED":
                sub.append(Check("enrollment", PASS, "재학 상태 ('{}')".format(phrase), scope))
            else:
                sub.append(Check("enrollment", FAIL, "재학생만 가능 ('{}', 학생: {})".format(phrase, {"LEAVE": "휴학", "GRADUATED": "졸업/수료"}.get(p.status, p.status)), scope))
        if vocab.GRADUATING in tags:
            sub.append(Check("enrollment", _bool_status(p.final_semester), "졸업학기 여부 확인 ('{}')".format(phrase), scope))
        if vocab.REMOTE in tags:
            if p.school_type is None:
                sub.append(Check("enrollment", UNKNOWN, "학교 종류 정보 없음 ('{}' 필요)".format(phrase), scope))
            else:
                ok = p.school_type == "REMOTE"
                sub.append(Check("enrollment", PASS if ok else FAIL, "원격·사이버대학 재학생 대상 ('{}')".format(phrase), scope))
        if not sub:
            sub.append(Check("enrollment", UNKNOWN, "학적 조건 확인: {}".format(phrase), scope))
        out.extend(sub)
    # ---- 제외 상태 (ENROLLED 태그는 제외 문구에서는 의미 없음: '졸업연기자(0학점 재학생)')
    for phrase in en.get("excluded_status") or []:
        out.extend(_excluded_phrase(phrase, p, scope, "enrollment"))
    # ---- 남은 학기
    mr = en.get("min_remaining_semesters")
    if mr is not None:
        if p.remaining_semesters is None:
            out.append(Check("enrollment", UNKNOWN, "졸업까지 남은 학기 정보 없음 (최소 {}학기 필요)".format(mr), scope))
        else:
            ok = p.remaining_semesters >= mr
            out.append(Check("enrollment", PASS if ok else FAIL, "남은 학기 {} (최소 {}학기 필요)".format(p.remaining_semesters, mr), scope))
    return out


def _excluded_phrase(phrase: str, p: StudentProfile, scope: str, key: str) -> List[Check]:
    tags = vocab.status_tags(phrase) - {vocab.ENROLLED}
    out: List[Check] = []
    if vocab.LEAVE in tags:
        out.append(Check(key, UNKNOWN if p.status is None else (FAIL if p.status == "LEAVE" else PASS),
                         "휴학생 제외", scope))
    if vocab.GRADUATED in tags:
        out.append(Check(key, UNKNOWN if p.status is None else (FAIL if p.status == "GRADUATED" else PASS), "졸업·수료생 제외", scope))
    if vocab.GRADUATING in tags:
        out.append(Check(key, _inv(p.final_semester), "졸업학기생 제외", scope))
    if vocab.EXTENDED in tags:
        out.append(Check(key, _inv(p.extended_enrollment), "정규학기 초과 등록자 제외", scope))
    if vocab.DEFERRED in tags:
        out.append(Check(key, _inv(p.graduation_deferred), "졸업유예·졸업연기자 제외", scope))
    if vocab.GRAD_STUDENT in tags:
        out.append(Check(key, UNKNOWN if p.level is None else (FAIL if p.level == "GRAD" else PASS), "대학원생 제외", scope))
    if vocab.REMOTE in tags:
        out.append(Check(key, UNKNOWN if p.school_type is None else (FAIL if p.school_type == "REMOTE" else PASS),
                         "원격·사이버대학·학점은행제 재학생 제외", scope))
    if vocab.TUITION_UNPAID in tags:
        out.append(Check(key, UNKNOWN if p.tuition_paid is None else (PASS if p.tuition_paid else FAIL), "등록금 미납·분납자 제외", scope))
    if not out and len(phrase.strip()) <= 12 and re.search(r"자퇴|퇴학|제적", phrase) and not re.search(r"휴학|졸업", phrase):
        # '자퇴' 같은 짧은 제외 문구: 지금 재학 중인 학생은 해당하지 않는다 (학적 상태를 모르면 확인 필요)
        out.append(Check(key, PASS if p.status == "ENROLLED" else UNKNOWN, "자퇴·퇴학·제적자 제외", scope))
    if not out:
        out.append(Check(key, UNKNOWN, "제외 조건 확인: {}".format(phrase), scope))
    return out


def _inv(flag: Optional[bool]) -> str:
    """'해당하면 제외' 조건: 해당(True)이면 FAIL, 아니면(False) PASS, 모르면 UNKNOWN."""
    return UNKNOWN if flag is None else (FAIL if flag else PASS)


_GPA_BASIS_LABEL = {"LAST_SEMESTER": "직전학기 평점", "CUMULATIVE": "전체(누적) 평점", "EITHER": "평점(직전학기 또는 전체)"}


def _student_gpa(p: StudentProfile, basis: Optional[str]) -> Optional[float]:
    if basis == "LAST_SEMESTER":
        return p.gpa_last
    if basis == "CUMULATIVE":
        return p.gpa_cumulative
    if basis == "EITHER":
        vals = [v for v in (p.gpa_last, p.gpa_cumulative) if v is not None]
        return max(vals) if vals else None
    return p.gpa_last if p.gpa_last is not None else p.gpa_cumulative


def _cmp_gpa(v: float, S: Optional[float], mn: float, sc: Optional[float], p: StudentProfile) -> str:
    if sc is None or S is None:
        return UNKNOWN
    if sc == S:
        return PASS if v >= mn - 1e-9 else FAIL
    if sc == 100.0:
        # 100점제는 4.x 평점과 단순 환산이 부정확하므로 학생이 100점 환산 점수를 입력한 경우에만 비교
        if p.gpa_percent is None:
            return UNKNOWN
        return PASS if p.gpa_percent >= mn - 1e-9 else FAIL
    conv = mn * S / sc                                   # 4.5/4.3/4.0/5.0 끼리는 비율 환산
    margin = SCALE_MARGIN * S
    if v >= conv + margin:
        return PASS
    if v < conv - margin:
        return FAIL
    return UNKNOWN


def check_gpa(gpa: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    mn, sc = gpa.get("min"), gpa.get("scale")
    cands = []
    if mn is not None:
        cands.append((mn, sc))
    for a in gpa.get("alternatives") or []:
        if isinstance(a, dict) and a.get("min") is not None:
            cands.append((a["min"], a.get("scale")))
    if not cands:
        return []
    desc = " 또는 ".join("{:g}/{} 이상".format(m, "{:g}".format(s) if s else "?") for m, s in cands)
    v = _student_gpa(p, gpa.get("basis"))
    if v is None:
        label = _GPA_BASIS_LABEL.get(gpa.get("basis"), "평점")
        return [Check("gpa", UNKNOWN, "{} 정보 없음 (기준: {})".format(label, desc), scope)]
    S = p.gpa_scale
    same = [c for c in cands if c[1] is not None and S is not None and c[1] == S]      # 같은 만점 기준이 있으면 그것만 사용
    use = same or cands
    status = _combine_any([_cmp_gpa(v, S, m, s, p) for m, s in use])
    msg = {PASS: "성적 충족 ({:g}/{:g}, 기준 {})", FAIL: "성적 미달 ({:g}/{:g}, 기준 {})",
           UNKNOWN: "성적 판정 불가 ({:g}/{:g}, 기준 {} - 만점 단위가 달라 환산 경계)"}[status]
    return [Check("gpa", status, msg.format(v, S or 0, desc), scope)]


def check_credits(cr: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    out: List[Check] = []
    m = cr.get("min_last_semester")
    if m is not None:
        if p.credits_last is None:
            out.append(Check("credits", UNKNOWN, "직전학기 취득학점 정보 없음 ({:g}학점 이상 필요)".format(m), scope))
        else:
            ok = p.credits_last >= m
            out.append(Check("credits", PASS if ok else FAIL, "직전학기 취득학점 {:g} (기준 {:g}학점 이상)".format(p.credits_last, m), scope))
    m2 = cr.get("min_completed_semesters")
    if m2 is not None:
        if p.semesters_completed is None:
            out.append(Check("credits", UNKNOWN, "등록 학기 수 정보 없음 ({:g}학기 이상 필요)".format(m2), scope))
        else:
            ok = p.semesters_completed >= m2
            out.append(Check("credits", PASS if ok else FAIL, "등록 {}학기 (기준 {:g}학기 이상)".format(p.semesters_completed, m2), scope))
    return out


def _bracket_status(p: StudentProfile, n: int) -> str:
    if p.income_bracket is None:
        return UNKNOWN
    return PASS if p.income_bracket <= n else FAIL


def check_income(inc: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    """eligible_types 는 '또는' 관계. max_bracket 은 학자금지원구간 선택지에만 적용."""
    types = inc.get("eligible_types") or []
    mb = inc.get("max_bracket")
    options: List[tuple] = []
    has_bracket_option = False
    for t in types:
        kind = vocab.income_kind(t)
        if kind == vocab.BASIC:
            options.append((t, _bool_status(p.is_basic_livelihood)))
        elif kind == vocab.NEAR_POOR:
            options.append((t, _bool_status(p.is_near_poor)))
        elif kind == vocab.SINGLE_PARENT:
            options.append((t, _bool_status(p.is_single_parent)))
        elif kind == vocab.BRACKET:
            has_bracket_option = True
            n = vocab.parse_bracket(t)
            n = int(mb) if n is None and mb is not None else n
            options.append((t, _bracket_status(p, n) if n is not None else UNKNOWN))
        else:
            options.append((t, UNKNOWN))
    if mb is not None and not has_bracket_option:
        options.append(("지원구간 {:g}구간 이내".format(mb), _bracket_status(p, int(mb))))
    if not options:
        return []
    status = _combine_any([s for _, s in options])
    labels = " / ".join(t for t, _ in options)
    msg = {PASS: "소득 조건 충족 (해당 유형: {})", FAIL: "소득 조건 미달 (필요: {} 중 하나)",
           UNKNOWN: "소득 정보 부족 (필요: {} 중 하나)"}[status]
    if status == PASS:
        labels = " / ".join(t for t, s in options if s == PASS)
    return [Check("income", status, msg.format(labels), scope)]


def _place_status(items: List[str], sido: Optional[str], sigungu: Optional[str]) -> str:
    sido_c, sig_c = vocab.sido_core(sido), vocab.place_core(sigungu)
    statuses = []
    for it in items:
        kind, core = vocab.classify_place(it)
        if kind == "SIDO":
            statuses.append(UNKNOWN if sido_c is None else (PASS if core == sido_c else FAIL))
        elif kind == "SIGUNGU":
            statuses.append(UNKNOWN if sig_c is None else (PASS if core == sig_c else FAIL))
        else:
            statuses.append(UNKNOWN)
    return _combine_any(statuses)


_ALT_QUALIFIER = re.compile(r"자녀|부모|가족|배우자|친족|출신|졸업|모교|고등학교")


def check_region(rg: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    out: List[Check] = []
    res, sch = rg.get("residence") or [], rg.get("school_location") or []
    notes = rg.get("notes")
    alt = bool(notes and _ALT_QUALIFIER.search(notes))        # '시민 또는 그 자녀', '출신 고교' 같은 본인 외 대체 요건
    if res:
        st = _place_status(res, p.residence_sido, p.residence_sigungu)
        unknown_msg = ("세부 거주지(읍·면·동·리) 확인 필요 (필요: {})" if all(vocab.classify_place(i)[0] == "OTHER" for i in res)
                       else "거주지 정보 부족 (필요: {})")
        if st == FAIL and alt:
            out.append(Check("region", UNKNOWN, "본인 주민등록 거주지는 다르지만 부모·자녀 거주나 출신 학교 같은 대체 요건이 있을 수 있어 확인 필요 "
                             "(필요: {} / 안내: {})".format(" / ".join(res), notes), scope))
        else:
            out.append(Check("region", st, {PASS: "주민등록 거주지 해당 ({})", FAIL: "주민등록 거주지 조건 미달 (필요: {})",
                                            UNKNOWN: unknown_msg}[st].format(" / ".join(res)), scope))
    if sch:
        st = _place_status(sch, p.school_sido, p.school_sigungu)
        out.append(Check("region", st, {PASS: "학교 소재지 해당 ({})", FAIL: "학교 소재지 조건 미달 (필요: {})",
                                        UNKNOWN: "학교 소재지 정보 부족 (필요: {})"}[st].format(" / ".join(sch)), scope))
    # 거주 기간 같은 세부 조건은 notes 에만 있다. 기간 표현이 있으면 코드가 판정하지 않고 확인 항목으로 둔다.
    if (res or sch) and notes and vocab.durations(notes):
        months = sorted(set(vocab.durations(notes)))
        single = len(months) == 1
        if single and p.residence_years is not None and res:
            need = months[0]
            ok = p.residence_years * 12 >= need
            out.append(Check("region", PASS if ok else FAIL, "거주 기간 {:g}년 (공고 기준 {}개월 이상: {})".format(p.residence_years, need, notes), scope))
        else:
            out.append(Check("region", UNKNOWN, "거주 기간 등 세부 지역 조건 확인: {}".format(notes), scope))
    return out


def check_nationality(nat: Optional[str], p: StudentProfile, scope: str) -> List[Check]:
    if not nat:
        return []
    kind = vocab.nationality_kind(nat)
    if kind is None or p.nationality is None:
        return [Check("nationality", UNKNOWN, "국적 조건 확인: {}".format(nat), scope)]
    ok = kind == p.nationality
    return [Check("nationality", PASS if ok else FAIL, "국적 조건 {} ({})".format("해당" if ok else "미해당", nat), scope)]


def check_majors(majors: List[str], p: StudentProfile, scope: str) -> List[Check]:
    if not majors:
        return []
    if p.major is None:
        return [Check("major", UNKNOWN, "학과 정보 없음 (공고 대상: {})".format(", ".join(majors)), scope)]
    hit = any(_norm_words(m) in _norm_words(p.major) or _norm_words(p.major) in _norm_words(m) for m in majors)
    return [Check("major", PASS if hit else UNKNOWN, "학과 {} ({})".format("해당" if hit else "표기가 달라 확인 필요", ", ".join(majors)), scope)]


def check_text_conditions(special: List[str], others: List[str], exclusions: List[str], p: StudentProfile, scope: str) -> List[Check]:
    out: List[Check] = []
    for s in special:
        hit = _text_matches_any(s, p.special_status)
        out.append(Check("special", UNKNOWN if hit is None else (PASS if hit else FAIL),
                         "특수 신분 {}: {}".format("해당" if hit else ("확인 필요" if hit is None else "미해당"), s), scope))
    for o in others:
        out.append(Check("other", UNKNOWN, "확인 필요: {}".format(o), scope))
    for ex in exclusions:
        tags = vocab.status_tags(ex) - {vocab.ENROLLED}
        if tags:
            out.extend(_excluded_phrase(ex, p, scope, "exclusion"))
        elif re.search(r"등록금", ex) and re.search(r"면제|자부담금이?\s*없", ex):
            out.append(Check("exclusion", _inv(p.tuition_fully_waived), "등록금 전액 면제자 제외", scope))
        else:
            out.append(Check("exclusion", UNKNOWN, "제외 조건 확인: {}".format(ex), scope))
    return out


# ================================================================== 구형식 보정
_VARIATION_HINT = re.compile(r"유형|트랙|별도|예외|각각|\[[^\]]+\]")


def is_v2(elig: Dict[str, Any]) -> bool:
    """스키마 v2(유형별 조건 variants 포함) 결과인지. 저장된 ELIG_JSON 은 schema_version 이 있고,
    버전 표시가 없어도 variants 가 채워져 있으면 v2 로 본다."""
    return (elig.get("schema_version") or 1) >= 2 or bool(elig.get("variants"))


def legacy_dims(elig: Dict[str, Any]) -> set:
    """schema_version < 2 결과에서 유형별 조건이 합쳐져 있을 수 있는 항목."""
    if is_v2(elig):
        return set()
    dims = set()
    for dim in ("gpa", "credits", "income"):
        notes = (elig.get(dim) or {}).get("notes") or ""
        if _VARIATION_HINT.search(notes):
            dims.add(dim)
    if any(re.match(r"\s*\[[^\]]+\]", c or "") for c in elig.get("other_conditions") or []):
        dims |= {"gpa", "credits", "income"}
    return dims


def _downgrade(c: Check) -> Check:
    return Check(c.key, UNKNOWN, c.message + " [구형식 결과: 유형별 조건이 합쳐져 있을 수 있어 확인 필요]", c.scope)


# ================================================================== 종합
def _section_checks(d: Dict[str, Any], p: StudentProfile, scope: str, include_text: bool = True) -> List[Check]:
    out: List[Check] = []
    out += check_gpa(d.get("gpa") or {}, p, scope)
    out += check_credits(d.get("credits") or {}, p, scope)
    out += check_income(d.get("income") or {}, p, scope)
    out += check_region(d.get("region") or {}, p, scope)
    return out


def _uniq(checks: List[Check]) -> List[Check]:
    """같은 판정·같은 문구의 중복 항목 제거 (순서 유지). 예: '사이버대학'/'학점은행제' 가 각각 원격대학 제외로 해석되는 경우"""
    seen, out = set(), []
    for c in checks:
        k = (c.key, c.status, c.message, c.scope)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _verdict(checks: List[Check]) -> str:
    if any(c.status == FAIL for c in checks):
        return NO_MATCH
    if any(c.status == UNKNOWN for c in checks):
        return CONDITIONAL
    return MATCH


CHECK_KEYS = ("levels", "grade", "enrollment", "gpa", "credits", "income", "region", "nationality", "major", "special", "other", "exclusion", "terms", "pending")


def _defer(c: Check) -> Check:
    return Check(c.key, UNKNOWN, c.message + " [AI 판단 예정: 코드는 탈락 처리하지 않음]", c.scope)


def _variant_checks_v2(v: Dict[str, Any], name: str, p: StudentProfile) -> List[Check]:
    checks: List[Check] = []
    checks += check_levels(v.get("target_levels") or [], p, name)
    checks += _section_checks(v, p, name)
    checks += check_text_conditions([], v.get("other_conditions") or [], [], p, name)
    return checks


def _finish(elig: Dict[str, Any], common: List[Check], variant_checks, defer) -> Evaluation:
    """공통 조건 결과와 유형별 판정을 합쳐 최종 결과를 낸다 (v2·v3 공통). variant_checks(유형, 이름) -> 그 유형의 검사 목록."""
    ev = Evaluation(verdict=NO_MATCH, common=common)
    needs_mod.annotate(common, elig)
    common_fail = [c.message for c in common if c.status == FAIL]
    if common_fail:
        ev.fail_reasons = common_fail
        return ev

    variants = elig.get("variants") or []
    if not variants:
        ev.verdict = _verdict(common)
        return ev

    results: List[VariantResult] = []
    for v in variants:
        name = v.get("name") or "(이름 없음)"
        checks = variant_checks(v, name)
        needs_mod.annotate(checks, v)
        if defer:
            checks = [_defer(c) if (c.status == FAIL and c.key in defer) else c for c in checks]
        checks = _uniq(checks)
        results.append(VariantResult(name=name, verdict=_verdict(checks), amount=v.get("amount"), checks=checks,
                                     apply_start=v.get("apply_start"), apply_end=v.get("apply_end")))
    ev.variants = results
    alive = [r for r in results if r.verdict != NO_MATCH]
    if not alive:
        ev.fail_reasons = ["해당하는 유형이 없음: " + "; ".join("{} ({})".format(r.name, r.fails[0]) for r in results if r.fails)]
        return ev
    best = MATCH if any(r.verdict == MATCH for r in alive) else CONDITIONAL
    has_common_unknown = any(c.status == UNKNOWN for c in common)
    ev.verdict = MATCH if (best == MATCH and not has_common_unknown) else CONDITIONAL
    return ev


def is_v3(elig: Dict[str, Any]) -> bool:
    return (elig.get("schema_version") or 1) >= 3


def evaluate(elig: Dict[str, Any], p: StudentProfile, defer_keys=(), regular_terms: int = 8) -> Evaluation:
    """defer_keys: 코드가 FAIL 로 단정하지 않고 UNKNOWN(확인 필요)으로 남길 조건 종류 (예: {'region'} - 나중에 AI 프롬프트로 판단).
    regular_terms: 정규 학기 수 (v3 판정이 남은 학기·졸업학기를 계산할 때 쓴다). schema_version >= 3 공고는 matcher_v3 가 판정한다."""
    if is_v3(elig):
        from . import matcher_v3                 # 순환 import 를 피하려고 여기서 불러온다
        return matcher_v3.evaluate_v3(elig, p, defer_keys, regular_terms)
    defer = set(defer_keys or ())
    common: List[Check] = []
    common += check_levels(elig.get("target_levels") or [], p, "공통")
    common += check_grades(elig.get("grades") or [], p, "공통")
    common += check_enrollment(elig.get("enrollment") or {}, p, "공통")
    common += _section_checks(elig, p, "공통")
    common += check_nationality(elig.get("nationality"), p, "공통")
    common += check_majors(elig.get("majors") or [], p, "공통")
    common += check_text_conditions(elig.get("special_status") or [], elig.get("other_conditions") or [],
                                    elig.get("exclusions") or [], p, "공통")
    dims = legacy_dims(elig)
    if dims:
        common = [_downgrade(c) if (c.status == FAIL and c.key in dims) else c for c in common]
    if defer:
        common = [_defer(c) if (c.status == FAIL and c.key in defer) else c for c in common]
    common = _uniq(common)
    return _finish(elig, common, lambda v, name: _variant_checks_v2(v, name, p), defer)
