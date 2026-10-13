# -*- coding: utf-8 -*-
"""[COMMON] 스키마 v3: 항목 사전 기반 확장 조건의 형식과 정규화.

- v2 의 모든 키는 그대로 유지하고, v3 는 '추가'만 한다 (v3 키는 값이 있을 때만 결과에 들어간다).
- AI 가 표준 값 목록에 없는 값을 쓰면 버리지 않고 unmodeled_conditions(사전에 없는 조건)로 옮겨서 조건이 사라지지 않게 한다.
- v3 는 설정(AI_SCHEMA_VERSION=3)으로 켠 경우에만 쓰인다. 기본값(2)에서는 이 모듈이 호출되지 않는다.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Dict, List, Optional, Set

from .schema import (APPLY_TYPES_V3, TEMPLATE, _d, _date, _num, _str, _strs)

# ------------------------------------------------------------------ 표준 값 (항목 사전·판정 코드와 공유)
REGION_TYPES = ("RESIDENCE_SELF", "RESIDENCE_PARENT", "GRADUATED_SCHOOL", "SCHOOL_LOCATION", "PARENT_WORKPLACE", "ACTUAL_RESIDENCE")
REGION_MODES = ("ANY", "ALL", "UNKNOWN")          # ANY = 하나만 충족해도 됨 / ALL = 모두 충족 / UNKNOWN = 공고에서 구분이 불명확
TRANSFER_MODES = ("EXCLUDED", "USE_PREV_SCHOOL", "NO_GPA_REQUIRED", "ALLOWED")
WAIVER_STUDENTS = ("NEW_STUDENT", "TRANSFER", "REENTRY", "RETURNING")
WAIVER_SKIP = ("gpa", "credits", "income", "region")
INCOME_BASIS = ("BRACKET", "MEDIAN_PERCENT", "ASSET", "WAGE_REF", "NARRATIVE")
BRACKET_TERMS = ("CURRENT_TERM", "LAST_TERM", "UNSPECIFIED")
SPECIAL_CODES = ("BASIC_LIVELIHOOD", "NEAR_POOR", "SINGLE_PARENT", "DISABLED", "DEFECTOR", "MULTICULTURAL", "VETERAN",
                 "MULTI_CHILD", "WORKER_CHILD", "OTHER")
SPECIAL_SCOPES = ("SELF", "FAMILY")
SPECIAL_MODES = ("REQUIRED", "PREFERRED")         # PREFERRED = 우대(자격 요건이 아님)
MAJOR_GROUPS = ("ENGINEERING", "NATURAL", "MEDICAL", "ARTS_PE", "HUMANITIES", "SOCIAL", "EDUCATION", "AGRICULTURE")
SCHOOL_TYPES = ("UNIV4", "COLLEGE")
SCHOOL_EXCLUDED = ("CYBER", "OPEN_UNIV", "COMPANY_UNIV")
SCHOOL_PROGRAMS = ("NATIONAL_SCHOLARSHIP", "LOCAL_TALENT", "HOUSING_STABILITY")
PREREQ_TYPES = ("NATIONAL_SCHOLARSHIP_APPLIED", "BRACKET_CALCULATED", "ADMISSION_OFFER", "EXCHANGE_SELECTED", "DEGREE_HELD", "OTHER")
RECOMMENDERS = ("PROFESSOR", "PRESIDENT", "PRINCIPAL", "DEAN", "AGENCY", "OTHER")
DEADLINE_KINDS = ("FOUNDATION", "SCHOOL_VARIES")  # SCHOOL_VARIES = 학교(대학)마다 추천 마감일이 다름
EXCLUSION_TYPES = ("CONCURRENT_LIVING", "CONCURRENT_TUITION", "CONCURRENT_ANY", "PRIOR_SAME", "PRIOR_RECENT", "OTHER")
UNMODELED_KINDS = ("ELIGIBILITY", "EXCLUSION", "BENEFIT_LIMIT", "PROCEDURE", "OBLIGATION", "REVIEW")
GENDER_WORDS = {"M": "M", "MALE": "M", "남": "M", "남자": "M", "남학생": "M", "남성": "M",
                "F": "F", "FEMALE": "F", "여": "F", "여자": "F", "여학생": "F", "여성": "F"}

# '※ 중복수혜 : 일부 가능' 같은 드림스폰 요약 표기는 조건이 아니다 (AI 가 조건처럼 적으면 걸러낸다). '불가'나 구체적인 제한은 조건이므로 남긴다.
_SUMMARY_TAG = re.compile(r"^\s*[※*•\-]?\s*중복\s*수혜\s*[:：]\s*(가능|일부\s*가능|정보\s*없음|미기재)\s*(\([^)]*\))?\s*$")


def is_summary_tag(text: Optional[str]) -> bool:
    return bool(text and _SUMMARY_TAG.match(text))


MAX_REQUIREMENTS = 8
MAX_UNMODELED = 20
MAX_UNMODELED_TEXT = 120
MAX_ITEMS = 12


def _build_template_v3() -> Dict[str, Any]:
    t = copy.deepcopy(TEMPLATE)
    t["gpa"].update({"terms": None, "scale_stated": None})
    t["income"].update({"basis": None, "bracket_term": None, "max_median_percent": None, "max_asset_krw": None})
    t["region"].update({"mode": None, "requirements": []})
    t.update({
        "completed_terms": {"min": None, "max": None},
        "remaining_terms": {"min": None, "max": None},
        "entry_years": [], "new_student_only": None, "transfer": {"mode": None},
        "waivers": [], "special_groups": [], "gender": None, "age": {"min": None, "max": None},
        "major_groups": [], "school": {"types": [], "excluded_types": [], "programs": []},
        "language_requirements": [], "prerequisites": [],
        "recommendation": {"required": None, "from": []}, "deadline_kind": None,
        "exclusion_items": [], "obligations": [], "review_criteria": [], "unmodeled_conditions": [],
    })
    return t


TEMPLATE_V3: Dict[str, Any] = _build_template_v3()          # v3 에서 AI 에게 보여주는 출력 형식

# 목록 항목(객체)의 키: 항목 사전의 notice_path 검증에 쓴다 (예: region.requirements[].min_years)
LIST_ITEM_KEYS: Dict[str, tuple] = {
    "region.requirements": ("type", "areas", "min_years", "years_basis"),
    "waivers": ("when", "skip"),
    "special_groups": ("code", "scope", "mode", "text"),
    "language_requirements": ("exam", "min_score", "level"),
    "prerequisites": ("type", "text"),
    "exclusion_items": ("type", "text"),
    "unmodeled_conditions": ("text", "section", "source", "kind"),
}


def _flatten(d: Dict[str, Any], prefix: str = "") -> Set[str]:
    out: Set[str] = set()
    for k, v in d.items():
        p = prefix + k
        out.add(p)
        if isinstance(v, dict):
            out |= _flatten(v, p + ".")
        elif isinstance(v, list) and p in LIST_ITEM_KEYS:
            out |= {"{}[].{}".format(p, x) for x in LIST_ITEM_KEYS[p]}
    return out


V3_PATHS: Set[str] = _flatten(TEMPLATE_V3)                  # 항목 사전의 notice_path 로 쓸 수 있는 경로 전체


# ------------------------------------------------------------------ 작은 도우미
def _code(v) -> Optional[str]:
    s = _str(v)
    return re.sub(r"[\s\-]+", "_", s).upper() if s else None


def _enum(v, allowed) -> Optional[str]:
    """입력을 표준 값 목록에서 찾아 돌려준다 (대소문자·공백·하이픈 차이는 무시하고, 표준 값의 원래 표기를 돌려준다)."""
    c = _code(v)
    if not c:
        return None
    for a in allowed:
        if a.upper() == c:
            return a
    return None


def _clean(n: float):
    return int(n) if float(n).is_integer() else n


def _cut(s: Optional[str], n: int) -> Optional[str]:
    return s[:n] if s else s


def _bool(v) -> Optional[bool]:
    return v if isinstance(v, bool) else None


Sink = Callable[..., None]


def _range(d, lo: float, hi: float, issue, prefix: str) -> Dict[str, Any]:
    d = _d(d)
    out: Dict[str, Any] = {}
    for k in ("min", "max"):
        n = _num(d.get(k))
        if n is not None and lo <= n <= hi:
            out[k] = _clean(n)
    if "min" in out and "max" in out and out["min"] > out["max"]:
        issue(prefix, "최솟값({})이 최댓값({})보다 큼".format(out["min"], out["max"]))
    return out


# ------------------------------------------------------------------ 그룹별 정규화
def gpa_extra(gp, g: Dict[str, Any], issue, prefix: str) -> None:
    """gpa 에 terms(직전 N개 학기), scale_stated(만점 명시 여부)를 더한다. g 를 직접 고친다."""
    gp = _d(gp)
    if g["basis"] == "LAST_N":
        n = _num(gp.get("terms"))
        if n is None or not (1 <= n <= 12):
            issue(prefix + ".terms", "성적 기준이 LAST_N(직전 N개 학기 평균)인데 학기 수(terms)가 없거나 1~12 범위를 벗어남")
        else:
            g["terms"] = int(n)
    ss = _bool(gp.get("scale_stated"))
    if g["min"] is not None and g["scale"] is None:
        ss = False                                           # 만점 미기재: 검수 사유가 아니라 '4.5 가정' 대상
    if ss is not None:
        g["scale_stated"] = ss


def income_extra(inc) -> Dict[str, Any]:
    inc = _d(inc)
    out: Dict[str, Any] = {}
    b = _enum(inc.get("basis"), INCOME_BASIS)
    if b:
        out["basis"] = b
    bt = _enum(inc.get("bracket_term"), BRACKET_TERMS)
    if bt:
        out["bracket_term"] = bt
    mp = _num(inc.get("max_median_percent"))
    if mp is not None and 0 < mp <= 1000:
        out["max_median_percent"] = _clean(mp)
    ak = _num(inc.get("max_asset_krw"))
    if ak is not None and ak > 0:
        out["max_asset_krw"] = _clean(ak)
    return out


def region_extra(rg, issue, prefix: str, sink: Optional[Sink] = None) -> Dict[str, Any]:
    """region.requirements(+mode). 형식을 알 수 없는 요건은 버리지 않고 sink 로 사전에 없는 조건에 넘긴다."""
    rg = _d(rg)
    raw = rg.get("requirements")
    reqs: List[Dict[str, Any]] = []
    for r in (raw if isinstance(raw, list) else []):
        r = _d(r)
        t = _enum(r.get("type"), REGION_TYPES)
        areas = _strs(r.get("areas"))
        if t is None:
            if sink and (_str(r.get("type")) or areas):
                sink("지역 요건(유형 불명: {}): {}".format(_str(r.get("type")) or "-", ", ".join(areas) or "-"), "ELIGIBILITY", "region")
            continue
        item: Dict[str, Any] = {"type": t, "areas": areas}
        my = _num(r.get("min_years"))
        if my is not None and 0 <= my <= 50:
            item["min_years"] = _clean(my)
        yb = _cut(_str(r.get("years_basis")), 40)
        if yb:
            item["years_basis"] = yb
        reqs.append(item)
        if len(reqs) >= MAX_REQUIREMENTS:
            break
    if not reqs:
        return {}
    mode = _enum(rg.get("mode"), REGION_MODES)
    if len(reqs) > 1 and mode in (None, "UNKNOWN"):
        issue(prefix + ".mode", "지역 요건이 둘 이상인데 '하나만 충족(ANY)'인지 '모두 충족(ALL)'인지 구분이 없음")
        mode = "UNKNOWN"
    return {"mode": mode or "ALL", "requirements": reqs}


def special_groups(raw, sink: Optional[Sink] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in (raw if isinstance(raw, list) else []):
        d = _d(it) if isinstance(it, dict) else {"code": it}
        code = _enum(d.get("code"), SPECIAL_CODES)
        text = _cut(_str(d.get("text")), 60)
        if code is None:
            if sink:
                sink("신분 조건(표준 코드 아님): {}".format(text or _str(d.get("code")) or "-"), "ELIGIBILITY", "special_groups")
            continue
        item = {"code": code, "scope": _enum(d.get("scope"), SPECIAL_SCOPES) or "SELF",
                "mode": _enum(d.get("mode"), SPECIAL_MODES) or "REQUIRED"}
        if code == "OTHER" and text:
            item["text"] = text
        key = (item["code"], item["scope"], item["mode"], item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


def waivers(raw, sink: Optional[Sink] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for w in (raw if isinstance(raw, list) else []):
        d = _d(w)
        when = _d(d.get("when"))
        stu = [_enum(x, WAIVER_STUDENTS) for x in _strs(when.get("student"))]
        spc = [_enum(x, SPECIAL_CODES) for x in _strs(when.get("special"))]
        skip = [_enum(x, WAIVER_SKIP) for x in _strs(d.get("skip"))]
        ok_stu, ok_spc, ok_skip = [x for x in stu if x], [x for x in spc if x], [x for x in skip if x]
        if not (ok_stu or ok_spc) or not ok_skip:
            if sink:
                sink("면제 조건(해석 불가): {}".format(_cut(_str(w) if not isinstance(w, dict) else str(w), 100)), "ELIGIBILITY", "waivers")
            continue
        when_out: Dict[str, Any] = {}
        if ok_stu:
            when_out["student"] = ok_stu
        if ok_spc:
            when_out["special"] = ok_spc
        out.append({"when": when_out, "skip": ok_skip})
        if len(out) >= 6:
            break
    return out


def _enum_list(raw, allowed, label: str, sink: Optional[Sink]) -> List[str]:
    out: List[str] = []
    for x in _strs(raw):
        c = _enum(x, allowed)
        if c:
            if c not in out:
                out.append(c)
        elif sink:
            sink("{}(표준 값 아님): {}".format(label, _cut(x, 60)), "ELIGIBILITY", label)
    return out


def language_requirements(raw) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in (raw if isinstance(raw, list) else []):
        d = _d(it)
        exam = _cut(_str(d.get("exam")), 40)
        if not exam:
            continue
        item: Dict[str, Any] = {"exam": exam}
        ms = _num(d.get("min_score"))
        if ms is not None and ms >= 0:
            item["min_score"] = _clean(ms)
        lv = _cut(_str(d.get("level")), 20)
        if lv:
            item["level"] = lv
        out.append(item)
        if len(out) >= 6:
            break
    return out


def prerequisites(raw) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in (raw if isinstance(raw, list) else []):
        d = _d(it) if isinstance(it, dict) else {"text": it}
        t = _enum(d.get("type"), PREREQ_TYPES)
        text = _cut(_str(d.get("text")) or (None if t else _str(d.get("type"))), 80)
        if t is None:
            if not text:
                continue
            t = "OTHER"
        item: Dict[str, Any] = {"type": t}
        if text:
            item["text"] = text
        out.append(item)
        if len(out) >= 8:
            break
    return out


def exclusion_items(raw) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in (raw if isinstance(raw, list) else []):
        d = _d(it) if isinstance(it, dict) else {"text": it}
        text = _cut(_str(d.get("text")), MAX_UNMODELED_TEXT)
        if not text or is_summary_tag(text):
            continue
        out.append({"type": _enum(d.get("type"), EXCLUSION_TYPES) or "OTHER", "text": text})
        if len(out) >= MAX_ITEMS:
            break
    return out


def _short_list(raw, limit: int = MAX_ITEMS, width: int = 100) -> List[str]:
    return [_cut(t, width) for t in _strs(raw)][:limit]


def unmodeled(raw, extra: List[Dict[str, str]], issue) -> List[Dict[str, str]]:
    """사전에 없는 조건 목록. AI 가 적은 것과 정규화 중에 옮겨진 것(extra)을 합친다."""
    out: List[Dict[str, str]] = []
    seen = set()
    cands: List[Dict[str, Any]] = [it if isinstance(it, dict) else {"text": it} for it in (raw if isinstance(raw, list) else [])]
    cands += extra
    dropped = 0
    for it in cands:
        d = _d(it)
        text = _cut(_str(d.get("text")), MAX_UNMODELED_TEXT)
        if not text or is_summary_tag(text):
            continue
        key = re.sub(r"\s+", "", text).lower()
        if key in seen:
            continue
        seen.add(key)
        if len(out) >= MAX_UNMODELED:
            dropped += 1
            continue
        out.append({"text": text, "section": _cut(_str(d.get("section")), 30) or "", "source": _cut(_str(d.get("source")), 30) or "",
                    "kind": _enum(d.get("kind"), UNMODELED_KINDS) or "ELIGIBILITY"})
    if dropped:
        issue("unmodeled_conditions", "사전에 없는 조건이 {}개를 넘어 {}개를 저장하지 못함".format(MAX_UNMODELED, dropped))
    return out


# ------------------------------------------------------------------ 최상위 / 유형(variants)
def apply_v3(obj: Dict[str, Any], out: Dict[str, Any], issue, extra: List[Dict[str, str]]) -> None:
    """v3 추가 키를 정규화해서 out 에 넣는다 (값이 있는 키만)."""
    def sink(text, kind="ELIGIBILITY", section=""):
        extra.append({"text": text, "kind": kind, "section": section, "source": "AI 출력"})

    for name, lo, hi in (("completed_terms", 0, 20), ("remaining_terms", 0, 20), ("age", 0, 100)):
        r = _range(obj.get(name), lo, hi, issue, name)
        if r:
            out[name] = r
    years = [int(n) for n in (_num(x) for x in (obj.get("entry_years") or [])) if n is not None and 1990 <= n <= 2100]
    if years:
        out["entry_years"] = sorted(set(years))
    nso = _bool(obj.get("new_student_only"))
    if nso is not None:
        out["new_student_only"] = nso
    tm = _enum(_d(obj.get("transfer")).get("mode"), TRANSFER_MODES)
    if tm:
        out["transfer"] = {"mode": tm}
    wv = waivers(obj.get("waivers"), sink)
    if wv:
        out["waivers"] = wv
    sg = special_groups(obj.get("special_groups"), sink)
    if sg:
        out["special_groups"] = sg
    gender = GENDER_WORDS.get(_code(obj.get("gender")) or "") or GENDER_WORDS.get(_str(obj.get("gender")) or "")
    if gender:
        out["gender"] = gender
    mg = _enum_list(obj.get("major_groups"), MAJOR_GROUPS, "전공 계열", sink)
    if mg:
        out["major_groups"] = mg
    sc = _d(obj.get("school"))
    school = {"types": _enum_list(sc.get("types"), SCHOOL_TYPES, "학교 종류", sink),
              "excluded_types": _enum_list(sc.get("excluded_types"), SCHOOL_EXCLUDED, "제외 학교 종류", sink),
              "programs": _enum_list(sc.get("programs"), SCHOOL_PROGRAMS, "참여 사업", sink)}
    if any(school.values()):
        out["school"] = {k: v for k, v in school.items() if v}
    lr = language_requirements(obj.get("language_requirements"))
    if lr:
        out["language_requirements"] = lr
    pr = prerequisites(obj.get("prerequisites"))
    if pr:
        out["prerequisites"] = pr
    rc = _d(obj.get("recommendation"))
    frm = _enum_list(rc.get("from"), RECOMMENDERS, "추천 주체", sink)
    req = _bool(rc.get("required"))
    if req is None and frm:
        req = True                                            # 추천 주체를 적었다면 추천이 필요하다는 뜻
    if req is not None or frm:
        out["recommendation"] = {"required": req, "from": frm}
    dk = _enum(obj.get("deadline_kind"), DEADLINE_KINDS)
    if dk:
        out["deadline_kind"] = dk
    ex = exclusion_items(obj.get("exclusion_items"))
    if ex:
        out["exclusion_items"] = ex
    for k in ("obligations", "review_criteria"):
        v = _short_list(obj.get(k))
        if v:
            out[k] = v

    um = unmodeled(obj.get("unmodeled_conditions"), extra, issue)
    if um:
        out["unmodeled_conditions"] = um
    # v2 판정 코드가 v3 결과에서도 '확인 필요' 문구를 보여줄 수 있게, 비어 있는 v2 목록을 채운다 (표시용)
    if not out.get("exclusions") and ex:
        out["exclusions"] = [x["text"] for x in ex]
    if not out.get("other_conditions"):
        elig = [u["text"] for u in um if u["kind"] == "ELIGIBILITY"]
        if elig:
            out["other_conditions"] = elig


def variant_extra(it: Dict[str, Any], v: Dict[str, Any], issue, pfx: str) -> Dict[str, Any]:
    """유형(variants) 한 개의 v3 추가 조건. 표준 값이 아닌 것은 v['other_conditions'] 로 옮긴다."""
    extra: List[Dict[str, str]] = []

    def sink(text, kind="ELIGIBILITY", section=""):
        extra.append({"text": text})

    out: Dict[str, Any] = {}
    for k in ("apply_start", "apply_end"):
        val, bad = _date(it.get(k))
        if bad:
            issue("{}.{}".format(pfx, k), "날짜 형식 오류: {!r} (YYYY-MM-DD 필요)".format(it.get(k)))
        if val:
            out[k] = val
    at = _enum(it.get("apply_type"), APPLY_TYPES_V3)
    if at and at != "UNKNOWN":
        out["apply_type"] = at
    sg = special_groups(it.get("special_groups"), sink)
    if sg:
        out["special_groups"] = sg
    nat = _cut(_str(it.get("nationality")), 40)
    if nat:
        out["nationality"] = nat
    gender = GENDER_WORDS.get(_code(it.get("gender")) or "") or GENDER_WORDS.get(_str(it.get("gender")) or "")
    if gender:
        out["gender"] = gender
    for name, lo, hi in (("completed_terms", 0, 20), ("remaining_terms", 0, 20), ("age", 0, 100)):
        r = _range(it.get(name), lo, hi, issue, "{}.{}".format(pfx, name))
        if r:
            out[name] = r
    mg = _enum_list(it.get("major_groups"), MAJOR_GROUPS, "전공 계열", sink)
    if mg:
        out["major_groups"] = mg
    years = [int(n) for n in (_num(x) for x in (it.get("entry_years") or [])) if n is not None and 1990 <= n <= 2100]
    if years:
        out["entry_years"] = sorted(set(years))
    tm = _enum(_d(it.get("transfer")).get("mode"), TRANSFER_MODES)
    if tm:
        out["transfer"] = {"mode": tm}
    if extra:
        v["other_conditions"] = list(v["other_conditions"]) + [e["text"] for e in extra]
    return out
