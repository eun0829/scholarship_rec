# -*- coding: utf-8 -*-
"""[RECOMMEND] 판정 엔진 v3 - 4-A: 지역 요건, 성적, 학기 조건, 이수학기 파생값.

schema_version >= 3 인 공고만 이 경로로 판정한다 (v2 이하 공고의 판정은 matcher.evaluate 가 그대로 한다).
판정 원칙은 v2 와 같다: 각 조건은 PASS / FAIL / UNKNOWN 이고, 학생 정보가 없거나 공고가 불명확하면 탈락시키지 않고 UNKNOWN(확인 필요)이다.

4-A 가 판정하는 것
  - 지역 요건(region.requirements): 본인·부모 주민등록, 졸업 고교 소재지, 학교 소재지, 거주 기간. ANY(하나만) / ALL(모두) / UNKNOWN(불명확)
  - 성적: 직전학기 / 누적 / 둘 중 하나 / 직전 N개 학기 평균 / 100점제 (만점이 적혀 있지 않으면 4.5 로 가정하고 알림)
  - 학기 조건: 이수 학기, 졸업까지 남은 학기 (+ 이수학기에서 계산한 남은 학기·졸업학기)
4-B1 이 더하는 것 (conditions_v3.py): 신입생 성적 면제와 면제(waivers), 필수 신분, 나이·성별·입학년도·편입, 학과 계열, 학교 종류,
  그리고 판정에 영향을 주지 않는 별도 표기(선행 조건, 우대 조건, 학교 참여 사업, 확인 사항).
아직 판정하지 않는 것: 소득의 중위소득 %·재산·직업 기준(자격 조건이라 '확인 필요'로 남긴다).
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from . import places, vocab
from .redundant import _BLOCK, _PUNCT, drop_redundant
from . import needs as needs_mod
from .conditions_v3 import (apply_waivers, build_notes, build_preferences, build_prerequisites, build_school_programs, check_exclusions_v3, check_extras,
                            check_special_required, is_new_student, merge_special_income, waiver_effects)
from .matcher import (CONDITIONAL, FAIL, MATCH, NO_MATCH, PASS, UNKNOWN, Check, Evaluation, _cmp_gpa, _combine_any, _defer, _finish, _uniq,
                      check_enrollment, check_grades, check_income, check_levels, check_majors, check_nationality, check_region, check_text_conditions)
from .profile import StudentProfile
from ..common.variant_scope import VARIANT_PREFIX, attach_prefixed_conditions, prefix_matches

DEFAULT_REGULAR_TERMS = 8                 # 4년제 학사의 정규 학기 수
ASSUMED_NOTICE_SCALE = 4.5                # 공고에 평점 만점이 없을 때의 가정 (결정 ②: 알리되 검수 사유로 삼지 않음)


# ================================================================== 이수학기 파생값
def derive_terms(p: StudentProfile, regular_terms: int = DEFAULT_REGULAR_TERMS) -> Tuple[StudentProfile, List[str]]:
    """남은 학기·졸업학기를 이수학기에서 계산한다 (학적 값이 이미 있으면 그 값을 쓴다). 원본 프로필은 바꾸지 않는다.

    이수학기 = 현재 학기를 제외하고 이미 등록을 마친 학기 수. 남은 학기 = 정규 학기 수 - 이수학기 (현재 학기 포함). 졸업학기 = 남은 학기가 1.
    정규 학기 수를 알 수 있는 경우(4년제 학사, 편입생 아님, 졸업유예·초과 등록 아님)에만 계산한다. 남은 학기가 0 이면(8학기를 마침)
    초과학기인지 졸업학기인지 알 수 없어 졸업학기 여부는 계산하지 않는다.
    """
    if p.semesters_completed is None:
        return p, []
    if (p.level != "UNIV" or p.school_type not in (None, "GENERAL") or p.admission_type == "TRANSFER" or p.status == "GRADUATED"
            or p.extended_enrollment or p.graduation_deferred):
        return p, []
    kw: Dict[str, Any] = {}
    notes: List[str] = []
    # 정규학기 초과 등록: 이수학기가 정규 학기 수보다 1 이상 많으면 확실히 초과, 정규 학기 수보다 2 이상 적으면(=7학기 이하) 확실히 아님.
    # 정규 학기 수와 같은 값(8)은 '현재 학기 포함 여부'에 따라 달라지므로 계산하지 않고 모름으로 둔다.
    if p.extended_enrollment is None:
        if p.semesters_completed > regular_terms:
            kw["extended_enrollment"] = True
            notes.append("이수학기 {}가 정규 {}학기를 넘어 정규학기 초과 등록으로 계산함".format(p.semesters_completed, regular_terms))
        elif p.semesters_completed < regular_terms:
            kw["extended_enrollment"] = False
    if not (p.remaining_semesters is not None and p.final_semester is not None):
        remaining = p.remaining_semesters if p.remaining_semesters is not None else max(0, regular_terms - p.semesters_completed)   # 학적 값이 우선
        if p.remaining_semesters is None:
            kw["remaining_semesters"] = remaining
        if p.final_semester is None and remaining >= 1:
            kw["final_semester"] = remaining == 1
        if "remaining_semesters" in kw or "final_semester" in kw:
            notes.append("남은 학기·졸업학기를 이수학기 {}와 정규 {}학기로 계산함 (학적 값이 있으면 그 값을 씀)".format(p.semesters_completed, regular_terms))
    if not kw:
        return p, []
    return dataclasses.replace(p, **kw), notes


# ================================================================== 학기 조건
def _range_text(mn: Optional[float], mx: Optional[float]) -> str:
    if mn is not None and mx is not None:
        return "{:g}~{:g}학기".format(mn, mx)
    return "{:g}학기 {}".format(mn if mn is not None else mx, "이상" if mn is not None else "이하")


def check_terms(d: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    out: List[Check] = []
    for key, label, value in (("completed_terms", "이수 학기", p.semesters_completed), ("remaining_terms", "졸업까지 남은 학기", p.remaining_semesters)):
        rng = d.get(key) or {}
        mn, mx = rng.get("min"), rng.get("max")
        if mn is None and mx is None:
            continue
        need = _range_text(mn, mx)
        if value is None:
            out.append(Check("terms", UNKNOWN, "{} 정보 없음 (기준 {})".format(label, need), scope))
            continue
        ok = (mn is None or value >= mn) and (mx is None or value <= mx)
        out.append(Check("terms", PASS if ok else FAIL, "{} {}학기 ({})".format(label, value, "기준 {} 충족".format(need) if ok else "기준 {} 미달".format(need)), scope))
    return out


def check_credits_v3(cr: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    """직전학기 취득학점은 성적 조건이라 신입생은 면제된다. 이수 학기 수는 학기 조건(terms)으로 따로 판정한다 (면제 대상이 아님)."""
    out: List[Check] = []
    m = cr.get("min_last_semester")
    if m is not None:
        if is_new_student(p) is True:
            out.append(Check("credits", PASS, "신입생: 직전학기 학점이 없어 학점 조건 면제 (기준 {:g}학점 이상)".format(m), scope))
        elif p.credits_last is None:
            out.append(Check("credits", UNKNOWN, "직전학기 취득학점 정보 없음 ({:g}학점 이상 필요)".format(m), scope))
        else:
            ok = p.credits_last >= m
            out.append(Check("credits", PASS if ok else FAIL, "직전학기 취득학점 {:g} (기준 {:g}학점 이상)".format(p.credits_last, m), scope))
    m2 = cr.get("min_completed_semesters")
    if m2 is not None:
        if p.semesters_completed is None:
            out.append(Check("terms", UNKNOWN, "등록 학기 수 정보 없음 ({:g}학기 이상 필요)".format(m2), scope))
        else:
            ok = p.semesters_completed >= m2
            out.append(Check("terms", PASS if ok else FAIL, "등록 {}학기 (기준 {:g}학기 이상)".format(p.semesters_completed, m2), scope))
    return out


# ================================================================== 성적
_BASIS_LABEL = {"LAST_SEMESTER": "직전학기 평점", "CUMULATIVE": "전체(누적) 평점", "EITHER": "평점(직전학기 또는 전체)", "UNKNOWN": "평점", "LAST_N": "직전 {}개 학기 평균 평점"}


def _last_n_status(p: StudentProfile, n: int, cmp) -> Tuple[str, str, str]:
    """직전 N개 학기 평균 -> (상태, 값 문구, 이유). 학점 가중 평균과 단순 평균이 모두 있으면 둘이 같은 판정일 때만 단정한다."""
    hist = p.term_history or []
    if len(hist) < n:
        return UNKNOWN, "", "학기별 성적 부족 (직전 {}개 학기 필요, 입력 {}개)".format(n, len(hist))
    rows = hist[:n]
    S = p.gpa_scale or 0
    simple = sum(r["gpa"] for r in rows) / n
    credits = [r.get("credits") for r in rows]
    if all(c for c in credits):
        weighted = sum(r["gpa"] * r["credits"] for r in rows) / sum(credits)
        s_simple, s_weighted = cmp(simple), cmp(weighted)
        if s_simple == s_weighted:
            return s_simple, "{:.2f}/{:g}".format(weighted, S), "단순 평균 {:.2f}, 학점 가중 평균 {:.2f}".format(simple, weighted)
        return UNKNOWN, "{:.2f}/{:g}".format(weighted, S), "단순 평균 {:.2f}과 학점 가중 평균 {:.2f}의 판정이 달라 확인 필요".format(simple, weighted)
    return cmp(simple), "{:.2f}/{:g}".format(simple, S), "단순 평균 (학점이 없어 가중 평균은 계산하지 못함)"


def check_gpa_v3(gpa: Dict[str, Any], p: StudentProfile, scope: str, assumptions: List[str]) -> List[Check]:
    cands: List[Tuple[float, Optional[float]]] = []
    if gpa.get("min") is not None:
        cands.append((gpa["min"], gpa.get("scale")))
    for a in gpa.get("alternatives") or []:
        if isinstance(a, dict) and a.get("min") is not None:
            cands.append((a["min"], a.get("scale")))
    if not cands:
        return []
    assumed = any(s is None for _, s in cands)
    cands = [(m, s if s is not None else ASSUMED_NOTICE_SCALE) for m, s in cands]
    if assumed:
        assumptions.append("공고에 평점 만점 표기가 없어 {:g} 만점으로 가정함".format(ASSUMED_NOTICE_SCALE))
    desc = " 또는 ".join("{:g}/{:g} 이상".format(m, s) for m, s in cands) + (" (만점 미기재: {:g} 가정)".format(ASSUMED_NOTICE_SCALE) if assumed else "")
    basis = gpa.get("basis") or "UNKNOWN"
    if basis != "ENTRANCE" and is_new_student(p) is True:                 # 신입생은 직전학기 성적이 없어 성적 조건이 적용되지 않는다 (입학성적 기준은 제외)
        return [Check("gpa", PASS, "신입생: 직전학기 성적이 없어 성적 조건 면제 (기준 {})".format(desc), scope)]
    S = p.gpa_scale
    same = [c for c in cands if S is not None and c[1] == S]            # 같은 만점 기준이 있으면 그것만 사용
    use = same or cands

    def cmp(v: float, last: bool = True) -> str:
        """last=False: 누적 평점. 100점제 기준은 직전학기 기준이면 직전학기 백분위로, 누적 기준이면 누적 백분위로만 비교한다 (서로 바꿔 쓰지 않는다)."""
        out = []
        for m, s in use:
            if s == 100.0 and S != 100.0 and not last:
                pc = p.gpa_cumulative_percent
                out.append(UNKNOWN if pc is None else (PASS if pc >= m - 1e-9 else FAIL))
            else:
                out.append(_cmp_gpa(v, S, m, s, p))
        return _combine_any(out)

    def make(status: str, value_text: str, label: str, why: str = "") -> List[Check]:
        head = {PASS: "성적 충족", FAIL: "성적 미달", UNKNOWN: "성적 판정 불가"}[status]
        if not why and status == UNKNOWN:
            if basis == "CUMULATIVE" and any(s == 100.0 for _, s in use) and S != 100.0 and p.gpa_cumulative_percent is None:
                why = "100점제 기준: 100점 환산 점수(누적 백분위)가 필요함"
            elif any(s == 100.0 for _, s in use) and S != 100.0 and p.gpa_percent is None:
                why = "100점제 기준: 100점 환산 점수(직전학기 백분위)가 필요함"
            elif any(s != S for _, s in use):
                why = "만점 단위가 달라 환산 경계"
        value = " " + value_text if value_text else ""
        return [Check("gpa", status, "{} ({}{}, 기준 {}){}".format(head, label, value, desc, " - " + why if why else ""), scope)]

    def missing(label: str) -> List[Check]:
        return [Check("gpa", UNKNOWN, "{} 정보 없음 (기준: {})".format(label, desc), scope)]

    if basis == "ENTRANCE":
        return [Check("gpa", UNKNOWN, "입학성적(수능·내신) 기준: 입력받지 않아 확인 필요 (기준: {})".format(desc), scope)]
    if basis == "LAST_N":
        n = gpa.get("terms")
        if not n:
            return [Check("gpa", UNKNOWN, "직전 몇 개 학기 평균인지 공고에 불명확 (기준: {})".format(desc), scope)]
        label = _BASIS_LABEL["LAST_N"].format(n)
        if any(s == 100.0 for _, s in use):
            return [Check("gpa", UNKNOWN, "{} 100점제 기준은 판정하지 않음 (기준: {})".format(label, desc), scope)]
        if n == 1:
            basis = "LAST_SEMESTER"
        else:
            status, value_text, why = _last_n_status(p, n, cmp)
            if status == UNKNOWN and not p.term_history:
                return missing(label)
            return make(status, value_text, label, why)
    label = _BASIS_LABEL.get(basis, "평점")
    if basis in ("LAST_SEMESTER", "CUMULATIVE"):
        v = p.gpa_last if basis == "LAST_SEMESTER" else p.gpa_cumulative
        if v is None and basis == "LAST_SEMESTER" and p.term_history and S:
            v = p.term_history[0]["gpa"]
        if v is None and basis == "CUMULATIVE" and p.gpa_cumulative_percent is not None and all(s == 100.0 for _, s in use):
            return make(cmp(0.0, False), "{:g}점(100점제)".format(p.gpa_cumulative_percent), label)           # 100점제 기준만 있으면 누적 평점 없이 누적 백분위로 비교
        if v is None:
            return missing(label)
        return make(cmp(v, basis == "LAST_SEMESTER"), "{:g}/{:g}".format(v, S or 0), label)
    # EITHER / UNKNOWN: 직전학기와 전체 중 하나라도 충족하면 충족. 둘 다 있어서 둘 다 미달일 때만 미달 (한쪽만 알면 다른 쪽이 충족일 수 있음)
    pairs = [(v, is_last) for v, is_last in ((p.gpa_last, True), (p.gpa_cumulative, False)) if v is not None]     # (값, 직전학기 여부)
    avail = [v for v, _ in pairs]
    if not avail:
        return missing(label)
    sts = [cmp(v, is_last) for v, is_last in pairs]
    if PASS in sts:
        return make(PASS, "{:g}/{:g}".format(max(avail), S or 0), label)
    if len(avail) == 2 and all(s == FAIL for s in sts):
        return make(FAIL, "직전 {:g}, 전체 {:g} / {:g}".format(p.gpa_last, p.gpa_cumulative, S or 0), label)
    return make(UNKNOWN, "{:g}/{:g}".format(avail[0], S or 0), label, "직전학기·전체 중 한쪽만 입력되어 다른 쪽이 충족일 수 있음" if len(avail) == 1 and sts[0] == FAIL else "")


# ================================================================== 학적 상태
_STATUS_LABEL = {"ENROLLED": "재학", "LEAVE": "휴학", "GRADUATED": "졸업·수료"}


def _status_is(p: StudentProfile, code: str) -> str:
    return UNKNOWN if p.status is None else (PASS if p.status == code else FAIL)


def check_enrollment_v3(en: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    """required_status 는 '둘 중 하나'다: 재학과 휴학은 동시에 될 수 없으므로 ['재학', '휴학'] 은 재학생도 휴학생도 가능하다는 뜻이다.
    (v2 는 목록을 모두 요구하는 것으로 읽어 '휴학'을 알 수 없는 조건으로 남겼다.) 원격대학 요건은 학교 종류 조건이라 따로 판정한다.
    제외 상태와 남은 학기는 v2 판정을 그대로 쓴다."""
    out: List[Check] = []
    alts: List[str] = []
    phrases: List[str] = []
    remote: List[str] = []
    for phrase in en.get("required_status") or []:
        tags = vocab.status_tags(phrase)
        if vocab.REMOTE in tags:
            remote.append(phrase)
            continue
        sts: List[str] = []
        if vocab.ENROLLED in tags:
            sts.append(_status_is(p, "ENROLLED"))
        if vocab.LEAVE in tags:
            sts.append(_status_is(p, "LEAVE"))
        if vocab.GRADUATED in tags:
            sts.append(_status_is(p, "GRADUATED"))
        if vocab.GRADUATING in tags:
            sts.append(UNKNOWN if p.final_semester is None else (PASS if p.final_semester else FAIL))
        alts.append(_combine_any(sts) if sts else UNKNOWN)             # 알 수 없는 표현('입학예정' 등)은 판정하지 않는다
        phrases.append(phrase)
    if alts:
        st = _combine_any(alts)
        need = " 또는 ".join(phrases)
        who = _STATUS_LABEL.get(p.status or "", "정보 없음")
        msg = {PASS: "학적 상태 해당 (필요: {})".format(need), FAIL: "학적 상태가 대상이 아님 (필요: {}, 학생: {})".format(need, who),
               UNKNOWN: "학적 상태 확인 필요 (필요: {}, 학생: {})".format(need, who)}[st]
        out.append(Check("enrollment", st, msg, scope))
    if remote:
        out += check_enrollment({"required_status": remote}, p, scope)
    out += check_enrollment(dict(en, required_status=[]), p, scope)
    return out


# ================================================================== 지역
_REQ_LABEL = {"RESIDENCE_SELF": "본인 주민등록", "RESIDENCE_PARENT": "부모 주민등록", "GRADUATED_SCHOOL": "졸업 고교 소재지", "SCHOOL_LOCATION": "학교 소재지",
              "PARENT_WORKPLACE": "부모 근무지", "ACTUAL_RESIDENCE": "실거주"}


def _student_place(rtype: str, p: StudentProfile) -> Optional[places.Place]:
    if rtype == "RESIDENCE_SELF":
        return places.student_place(p.residence_sido, p.residence_sigungu)
    if rtype == "RESIDENCE_PARENT":
        return places.student_place(p.parent_residence_sido, p.parent_residence_sigungu)
    if rtype == "GRADUATED_SCHOOL":
        return places.student_place(p.hs_sido, p.hs_sigungu)
    if rtype == "SCHOOL_LOCATION":
        return places.student_place(p.school_sido, p.school_sigungu)
    return None                                                          # 부모 근무지·실거주: 학생 입력 항목이 없다


def _years_status(p: StudentProfile, need: float) -> Tuple[str, str]:
    """본인 거주 기간. 하한이 기준 이상이면 PASS, 구간의 상한이 기준 미만이면 FAIL, 그 사이는 UNKNOWN."""
    y = p.residence_years
    if y is None:
        return UNKNOWN, "본인 거주 기간 정보 없음 ({:g}년 이상 필요)".format(need)
    if y >= need:
        return PASS, ""
    if p.residence_years_open:
        return UNKNOWN, "거주 기간이 {:g}년 이상으로만 입력되어 {:g}년 충족 여부 확인 필요".format(y, need)
    upper = p.residence_years_upper if p.residence_years_upper is not None else y
    if upper >= need:
        return UNKNOWN, "거주 기간이 {:g}~{:g}년 구간이라 {:g}년 충족 여부 확인 필요".format(y, upper, need)
    return FAIL, "거주 기간 {:g}년 (필요 {:g}년 이상)".format(upper, need)


def _req_text(r: Dict[str, Any]) -> str:
    areas = " / ".join(a for a in r.get("areas") or [] if a) or "(지역 불명)"
    yrs = " {:g}년 이상".format(r["min_years"]) if r.get("min_years") else ""
    return "{} {}{}".format(_REQ_LABEL.get(r.get("type"), r.get("type")), areas, yrs)


def _eval_requirement(r: Dict[str, Any], p: StudentProfile) -> Tuple[str, str, str, tuple]:
    """요건 하나 -> (상태, 요건 문구, 이유, 풀어 줄 입력 항목 후보)"""
    t = r.get("type")
    label = _REQ_LABEL.get(t, str(t))
    text = _req_text(r)
    areas = [a for a in r.get("areas") or [] if a]
    code = needs_mod.PLACE_CODE.get(t)                                    # 본인 주민등록 / 부모 주민등록 / 졸업 고교 소재지
    if not areas:
        return UNKNOWN, text, "지역 정보가 공고에 불명확", ()
    sp = _student_place(t, p)
    if sp is None:
        return UNKNOWN, text, "{} 정보는 입력받지 않아 확인 필요".format(label), ()
    if sp.empty:
        if t == "SCHOOL_LOCATION":
            return UNKNOWN, text, "학교 소재지 설정이 없음 (.env 의 RECOMMEND_SCHOOL_SIDO / RECOMMEND_SCHOOL_SIGUNGU)", ()
        return UNKNOWN, text, "학생의 {} 정보 없음".format(label), ((code,) if code else ())
    results = [places.compare(places.parse_place(a), sp) for a in areas]
    status = places.combine_any([s for s, _ in results])
    place_unknown = status == UNKNOWN                                      # 거주 기간 등을 따지기 전, 지명 비교 자체가 확인 필요인가
    why = "" if status == PASS else next(w for s, w in results if s == status)
    need = r.get("min_years")
    basis_text = r.get("years_basis") or ""
    if status == PASS and not need and re.search(r"\d+\s*(년|개월)", basis_text):        # 기간을 숫자로 담지 못한 요건(예: '중·고교 3년 재학 후 졸업')은 입력 항목이 없어 확인 필요
        status, why = UNKNOWN, "{} 기간 조건('{}')은 입력받지 않아 확인 필요".format(label, basis_text[:30])
    if need:
        ys, ywhy = _years_status(p, need) if t == "RESIDENCE_SELF" else (UNKNOWN, "{} 거주·재학 기간은 입력받지 않아 확인 필요".format(label))
        if status == PASS and ys != PASS:
            status, why = ys, ywhy
        elif status != FAIL and ys == FAIL:
            status, why = FAIL, ywhy
        elif status == UNKNOWN and ys == UNKNOWN and ywhy:
            why = "{}; {}".format(why, ywhy)
    asks: tuple = ()
    if status == UNKNOWN:
        if code and place_unknown:
            asks = (code,)                                                 # 시·군·구까지 적지 않은 경우 등은 같은 항목을 더 자세히 답하면 달라진다 (이미 다 적었으면 질문에서 걸러진다)
        if need and t == "RESIDENCE_SELF" and p.residence_years is None:
            asks += ("RESY",)
    return status, text, why, asks


def _combine_mode(statuses: List[str], mode: str) -> str:
    if mode == "ALL":
        return FAIL if FAIL in statuses else (PASS if all(s == PASS for s in statuses) else UNKNOWN)
    if mode == "ANY":
        return _combine_any(statuses)
    return PASS if all(s == PASS for s in statuses) else (FAIL if all(s == FAIL for s in statuses) else UNKNOWN)   # 모르면 두 해석에서 모두 같을 때만 단정


def check_region_v3(rg: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    reqs = [r for r in rg.get("requirements") or [] if isinstance(r, dict)]
    if not reqs:
        return check_region(rg, p, scope)                                # v2 식(residence / school_location)만 있는 경우
    mode = rg.get("mode") or "UNKNOWN"
    evals = [_eval_requirement(r, p) for r in reqs]
    status = _combine_mode([s for s, _, _, _ in evals], mode)
    joiner = " 그리고 " if mode == "ALL" else " 또는 "
    need = joiner.join("[{}]".format(t) for _, t, _, _ in evals)
    notes = rg.get("notes")
    tail = " · 참고: {}".format(notes[:60]) if notes else ""
    if status == PASS:
        ok = [t for s, t, _, _ in evals if s == PASS]
        msg = "지역 조건 충족 (해당: {})".format(", ".join(ok))
    elif status == FAIL:
        msg = "지역 조건 미달 (필요: {})".format(need)
    else:
        unk = "; ".join("{}: {}".format(t, w) for s, t, w, _ in evals if s == UNKNOWN and w)
        mode_note = " (요건이 '또는'인지 '그리고'인지 공고에 불명확)" if mode == "UNKNOWN" and len(evals) > 1 else ""
        msg = "지역 확인 필요 (필요: {}){} - {}".format(need, mode_note, unk) if unk else "지역 확인 필요 (필요: {}){}".format(need, mode_note)
    asks = needs_mod.merge(*[n for s, _, _, n in evals if s == UNKNOWN]) if status == UNKNOWN else ()
    return [Check("region", status, msg + tail, scope, needs=asks)]


# ================================================================== 아직 판정하지 않는 자격 조건 (소득 확장)
def pending_checks(d: Dict[str, Any], scope: str) -> List[Check]:
    """중위소득 %·재산·직업 기준·서술형 소득은 지원구간으로 환산할 수 없는 자격 조건이라 무시하지 않고 '확인 필요'로 남긴다."""
    inc = d.get("income") or {}
    if inc.get("max_median_percent") is not None or inc.get("max_asset_krw") is not None or inc.get("basis") in ("MEDIAN_PERCENT", "ASSET", "WAGE_REF", "NARRATIVE"):
        return [Check("pending", UNKNOWN, "확인 필요: 소득 조건(중위소득·재산·직업 기준)은 자동 판정하지 않음", scope)]
    return []


# ================================================================== 종합
def _sections(d: Dict[str, Any], p: StudentProfile, scope: str, assumptions: List[str], top: bool, skip_special: bool = False,
              parent_special: Optional[Check] = None) -> List[Check]:
    """skip_special: 공고 전체의 필수 신분을 유형 안에서 소득과 함께 판정하므로 여기서는 하지 않는다. parent_special: 그렇게 내려받은 신분 판정."""
    out: List[Check] = []
    out += check_gpa_v3(d.get("gpa") or {}, p, scope, assumptions)
    out += check_credits_v3(d.get("credits") or {}, p, scope)
    out += check_terms(d, p, scope)
    inc = check_income(d.get("income") or {}, p, scope)
    needs_mod.annotate(inc, d)                                            # 신분과 결합할 때 소득 쪽 후보를 알아야 한다
    sp = None if skip_special else check_special_required(d.get("special_groups") or [], p, scope)
    if parent_special is not None:
        if sp is None and inc:
            sp = parent_special                                               # 이 유형의 소득 조건과 같은 방식으로 결합
        else:
            out.append(parent_special)
    if sp is not None and inc:
        out.append(merge_special_income(sp, inc[0]))                      # 같은 곳의 신분·소득 조건은 '또는'일 수 있어 함께 판정
    else:
        out += inc
        if sp is not None:
            out.append(sp)
    out += check_region_v3(d.get("region") or {}, p, scope)
    out += check_extras(d, p, scope, top, check_nationality)
    return out


def _variant_has_conditions(v: Dict[str, Any]) -> bool:
    """유형에 구조로 담긴 조건이 하나라도 있는가. 이름과 마감일뿐인 유형은 조건이 공고문에만 있는 것이다('유형별 선발조건은 공고문 확인')."""
    if any(v.get(k) for k in ("target_levels", "major_groups", "entry_years", "other_conditions", "nationality", "gender")):
        return True
    if any(isinstance(g, dict) and g.get("mode") == "REQUIRED" for g in v.get("special_groups") or []):      # 우대 신분은 자격 조건이 아니다
        return True
    g, c, inc, r = v.get("gpa") or {}, v.get("credits") or {}, v.get("income") or {}, v.get("region") or {}
    if g.get("min") is not None or g.get("alternatives"):
        return True
    if c.get("min_last_semester") is not None or c.get("min_completed_semesters") is not None:
        return True
    if (inc.get("max_bracket") is not None or inc.get("eligible_types") or inc.get("max_median_percent") is not None or inc.get("max_asset_krw") is not None
            or inc.get("basis") in ("MEDIAN_PERCENT", "ASSET", "WAGE_REF", "NARRATIVE")):
        return True
    if r.get("requirements") or r.get("residence") or r.get("school_location"):
        return True
    if any(any(x is not None for x in (v.get(k) or {}).values()) for k in ("age", "completed_terms", "remaining_terms")):
        return True
    return bool((v.get("transfer") or {}).get("mode"))


_REGULAR_TERM = re.compile(r"정규\s*(?:\d+\s*)?학기\s*(?:이내|내)(?:에)?\s*(?:졸업\s*예정|졸업\s*가능|졸업)")
_REGULAR_REST = ("예정자", "예정", "가능자", "가능", "재학생", "학생", "자", "인", "으로", "이며", "이고", "한", "에", "에서")


def _is_regular_term_sentence(text: str) -> bool:
    """'정규 학기 이내 졸업예정' 처럼 그 뜻만 담은 짧은 문장인가 (다른 조건이 섞였거나 부정·제한 표현이 있으면 아니다)."""
    s = (text or "").strip()
    if not s or _BLOCK.search(s) or not _REGULAR_TERM.search(s):
        return False
    rest = _REGULAR_TERM.sub("", s)
    for w in _REGULAR_REST:
        rest = rest.replace(w, "")
    return _PUNCT.sub("", rest) == ""


def regular_term_checks(texts: List[str], p: StudentProfile, regular_terms: int, scope: str) -> Tuple[List[Check], List[str]]:
    """'정규 학기 이내 졸업예정' 문장은 이수학기로 계산한 정규학기 초과 여부로 판정한다 (모호하면 확인 필요 그대로).
    p 는 derive_terms 를 거친 프로필: 초과 등록 False(이수학기 < 정규 학기 수)면 충족, True·졸업유예면 미달, 모르면 문장을 확인 항목으로 남긴다."""
    out: List[Check] = []
    rest: List[str] = []
    for t in texts:
        if not _is_regular_term_sentence(t):
            rest.append(t)
            continue
        info = "이수 {}학기, 정규 {}학기".format(p.semesters_completed, regular_terms)
        if p.extended_enrollment is False and not p.graduation_deferred and p.semesters_completed is not None:
            out.append(Check("terms", PASS, "정규 학기 이내 졸업예정 해당 ({})".format(info), scope))
        elif p.extended_enrollment is True or p.graduation_deferred:
            out.append(Check("terms", FAIL, "정규 학기 이내 졸업예정 미달 ({}, 정규학기 초과 등록 또는 졸업유예)".format(info if p.semesters_completed is not None else "정규 {}학기".format(regular_terms)), scope))
        else:
            rest.append(t)
    return out, rest


def _uses_derived(elig: Dict[str, Any]) -> bool:
    """공고가 남은 학기·졸업학기 조건을 쓰는가 (그럴 때만 계산한 값을 썼다고 알린다)."""
    if any(isinstance(b, dict) and b.get("remaining_terms") for b in [elig] + list(elig.get("variants") or [])):
        return True
    en = elig.get("enrollment") or {}
    if en.get("min_remaining_semesters") is not None:
        return True
    if any(_is_regular_term_sentence(t) for b in [elig] + list(elig.get("variants") or []) if isinstance(b, dict) for t in b.get("other_conditions") or []):
        return True
    return any(vocab.GRADUATING in vocab.status_tags(ph) for ph in (en.get("required_status") or []) + (en.get("excluded_status") or []))


_PROMOTION = re.compile(r"진급")


def _mentions_promotion(elig: Dict[str, Any]) -> bool:
    """공고 문장에 '진급'(예: '3학년 진급 재학생')이 있는가. 대상 학년이 지금 학년인지 올라갈 학년인지 모호한 표현이다."""
    texts: List[str] = list(elig.get("other_conditions") or [])
    texts += [u.get("text", "") for u in elig.get("unmodeled_conditions") or [] if isinstance(u, dict)]
    texts += [(elig.get("enrollment") or {}).get("notes") or "", elig.get("summary") or ""]
    return any(isinstance(t, str) and _PROMOTION.search(t) for t in texts)


def check_grades_v3(elig: Dict[str, Any], p: StudentProfile, scope: str) -> List[Check]:
    """학년 조건. '진급' 문장이 있고 학생이 대상 학년보다 한 학년 아래이며 2학기 중(이수 학기가 홀수)이면, 곧 대상 학년이 되는 학생일 수 있으므로
    미달로 확정하지 않고 확인 필요로 둔다 (1학기 중이거나 두 학년 이상 아래면 지금처럼 미달)."""
    grades = elig.get("grades") or []
    out = check_grades(grades, p, scope)
    if not out or out[0].status != FAIL or p.grade is None or not _mentions_promotion(elig):
        return out
    if (p.grade + 1) in grades and (p.semesters_completed is None or p.semesters_completed % 2 == 1):
        return [Check("grade", UNKNOWN, "{}학년이지만 공고에 '진급' 표현이 있어 확인 필요 (대상 학년: {} - 진급 예정이면 해당)".format(p.grade, grades), scope)]
    return out


_VARIANT_PREFIX = VARIANT_PREFIX
_prefix_matches = prefix_matches                    # 규칙은 common/variant_scope.py (저장할 때와 같은 함수)

# 고등학생 전용 문장: 대학생·대학원생 추천에서는 판정하지도 안내하지도 않는다.
# 신입생·입학 관련 문장(예: '신입생 고교 내신')은 대학생에게도 뜻이 있을 수 있어 남긴다.
_HIGH_SCHOOL_ONLY = re.compile(r"고등학생|고교생|고등학교\s*재학|고\s*[123]\s*(?:학년|학기)?[^가-힣]{0,4}.*내신|고등학교.*내신")
_HS_KEEP = re.compile(r"대학생|대학원|신입|편입")


def is_high_school_only(text: Any) -> bool:
    t = text if isinstance(text, str) else ""
    return bool(t) and bool(_HIGH_SCHOOL_ONLY.search(t)) and not _HS_KEEP.search(t)


def drop_conditions_of_failed_variants(elig: Dict[str, Any], failed_names) -> Dict[str, Any]:
    """공통 other_conditions 중 '유형이름: ...' 으로 시작하고 그 유형이 이미 미달인 문장을 뺀다.
    AI 가 유형별 문장을 공통에 적어 두면, 학생이 해당하지 않는 유형의 조건까지 확인 항목으로 남기 때문이다.
    조건부이거나 충족인 유형의 문장은 건드리지 않는다 (그 문장이 공통 판정을 보수적으로 지켜 주므로)."""
    names = {re.sub(r"\s+", "", n) for n in failed_names or ()}
    common = elig.get("other_conditions") or []
    if not names or not common:
        return elig
    keep = []
    for text in common:
        m = _VARIANT_PREFIX.match(text) if isinstance(text, str) else None
        if m and _prefix_matches(m.group(1), names):
            continue
        keep.append(text)
    return elig if len(keep) == len(common) else dict(elig, other_conditions=keep)


def drop_high_school_conditions(elig: Dict[str, Any], drop_pre_college_variants: bool = True) -> Dict[str, Any]:
    """공통·유형의 other_conditions, unmodeled_conditions, exclusions 에서 고등학생 전용 문장을 뺀다 (대학생·대학원생 추천용이므로)."""
    def clean(d: Dict[str, Any]) -> Dict[str, Any]:
        out = d
        oc = d.get("other_conditions")
        if oc and any(is_high_school_only(t) for t in oc):
            out = dict(out, other_conditions=[t for t in oc if not is_high_school_only(t)])
        um = d.get("unmodeled_conditions")
        if um and any(isinstance(u, dict) and is_high_school_only(u.get("text")) for u in um):
            out = dict(out, unmodeled_conditions=[u for u in um if not (isinstance(u, dict) and is_high_school_only(u.get("text")))])
        return out

    out = clean(elig)
    vs = elig.get("variants")
    if vs:
        # 중·고등학생만 대상인 유형은 통째로 뺀다 (예전에 저장된 공고 처리. 새로 추출하는 공고는 저장 전에 이미 빠진다)
        pre = [v for v in vs if isinstance(v, dict) and v.get("target_levels") and all(x in ("ELEM", "MIDDLE", "HIGH") for x in v["target_levels"])]
        if drop_pre_college_variants and pre and len(pre) < len(vs):
            vs = [v for v in vs if not any(v is q for q in pre)]
            out = dict(out, variants=vs)
        nv = [clean(v) if isinstance(v, dict) else v for v in vs]
        if any(a is not b for a, b in zip(nv, vs)):
            out = dict(out, variants=nv)
    return out


def evaluate_v3(elig: Dict[str, Any], p: StudentProfile, defer_keys=(), regular_terms: int = DEFAULT_REGULAR_TERMS) -> Evaluation:
    elig = attach_prefixed_conditions(elig)                                                 # '유형명: 조건' 문장은 그 유형의 조건으로 (예전에 저장된 공고도 같은 규칙)
    elig = drop_high_school_conditions(elig, p.level not in ("ELEM", "MIDDLE", "HIGH"))     # 중·고등학생 유형은 대학생·대학원생 판정에서만 뺀다 (옮겨진 고등학생 문장도 여기서 빠진다)
    ev = _evaluate_v3(elig, p, defer_keys, regular_terms)
    failed = [v.name for v in ev.variants if v.verdict == NO_MATCH]
    trimmed = drop_conditions_of_failed_variants(elig, failed)
    return ev if trimmed is elig else _evaluate_v3(trimmed, p, defer_keys, regular_terms)


def _evaluate_v3(elig: Dict[str, Any], p: StudentProfile, defer_keys=(), regular_terms: int = DEFAULT_REGULAR_TERMS) -> Evaluation:
    defer = set(defer_keys or ())
    p3, notes = derive_terms(p, regular_terms)
    assumptions: List[str] = list(notes) if _uses_derived(elig) else []
    waived, maybe = waiver_effects(elig, p3)
    excl_checks, excl_rest = check_exclusions_v3([x.get("text", "") for x in elig.get("exclusion_items") or [] if isinstance(x, dict)] or elig.get("exclusions") or [], p3, "공통")

    # 공고 전체에 필수 신분이 있고 소득 상한은 유형 안에만 있으면(예: 신분은 공통, 소득 6구간은 '대학생' 유형), 신분과 소득이 '또는'인지 '그리고'인지
    # 불명확하므로 신분을 유형으로 내려서 소득과 함께 판정한다 (한쪽만 충족이면 확인 필요).
    parent_sp = check_special_required(elig.get("special_groups") or [], p3, "공통")
    cross = (parent_sp is not None and not check_income(elig.get("income") or {}, p3, "공통")
             and any(isinstance(v, dict) and check_income(v.get("income") or {}, p3, "x") for v in elig.get("variants") or []))

    common: List[Check] = []
    common += check_levels(elig.get("target_levels") or [], p3, "공통")
    common += check_grades_v3(elig, p3, "공통")
    common += check_enrollment_v3(elig.get("enrollment") or {}, p3, "공통")
    common += _sections(elig, p3, "공통", assumptions, top=True, skip_special=cross)
    common += check_nationality(elig.get("nationality"), p3, "공통")
    common += check_majors(elig.get("majors") or [], p3, "공통")
    rt_common, others_common = regular_term_checks(drop_redundant(elig.get("other_conditions") or [], elig), p3, regular_terms, "공통")
    common += rt_common
    common += check_text_conditions([], others_common, [], p3, "공통")      # 자격 문장은 코드가 해석하지 않고 확인 항목으로 (이미 판정한 조건과 같은 뜻의 문장은 뺀다. '정규 학기 이내 졸업예정' 만 위에서 계산)
    common += excl_checks
    common += pending_checks(elig, "공통")
    common = apply_waivers(common, waived, maybe)
    if defer:
        common = [_defer(c) if (c.status == FAIL and c.key in defer) else c for c in common]
    common = _uniq(common)

    def variant_checks(v: Dict[str, Any], name: str) -> List[Check]:
        checks: List[Check] = []
        checks += check_levels(v.get("target_levels") or [], p3, name)
        checks += _sections(v, p3, name, assumptions, top=False, parent_special=Check("special", parent_sp.status, parent_sp.message, name) if cross else None)
        rt_v, others_v = regular_term_checks(drop_redundant(v.get("other_conditions") or [], elig, v), p3, regular_terms, name)
        checks += rt_v
        checks += check_text_conditions([], others_v, [], p3, name)
        checks += pending_checks(v, name)
        if not _variant_has_conditions(v):
            checks.append(Check("other", UNKNOWN, "이 유형의 선발 조건이 공고 본문에 없음 (공고문 확인 필요)", name))
        return apply_waivers(checks, waived, maybe)

    ev = _finish(elig, common, variant_checks, defer)
    ev.assumptions = list(dict.fromkeys(assumptions))
    # 판정에 영향을 주지 않고 따로 표시하는 항목
    ev.prerequisites = build_prerequisites(elig, p3)
    ev.preferences = build_preferences(elig, p3)
    ev.school_programs = build_school_programs(elig, p3)
    ev.notes = build_notes(elig, excl_rest)
    for vr, v in zip(ev.variants, elig.get("variants") or []):
        vr.preferences = build_preferences(v, p3)
    return ev
