# -*- coding: utf-8 -*-
"""[COMMON] AI 추출 결과(JSON)의 구조 정의, 정규화, 코드 검증.

11g 에는 JSON 검증 함수가 없으므로 DB 저장 전에 여기서 검증한다.
외부01/외부02 는 이 스키마를 그대로 공유하고, 교내 장학금 판정 항목과 맞출 때도 이 파일을 기준으로 삼는다.
"""
from __future__ import annotations

import ast
import copy
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

CATEGORIES = ("OUT", "IN", "NATIONAL", "LOAN", "INFO", "ETC")
APPLY_TYPES = ("SCHOOL", "DIRECT", "UNKNOWN")
LEVELS = ("ELEM", "MIDDLE", "HIGH", "UNIV", "GRAD")
GPA_BASIS = ("LAST_SEMESTER", "CUMULATIVE", "EITHER", "UNKNOWN")
UNCERTAIN_LEVELS = ("verify", "note")          # verify = 값이 틀렸거나 빠졌을 수 있어 사람이 확인 / note = 단순 참고
KNOWN_SCALES = (4.5, 4.3, 4.0, 5.0, 100.0)
WEEKDAYS = "월화수목금토일"
SCHEMA_VERSION = 2                              # 1: 초기 형식 / 2: variants(유형별 조건), apply_how 추가
SCHEMA_VERSION_V3 = 3                           # 3: 항목 사전 기반 확장 조건 + 사전에 없는 조건 목록 (AI_SCHEMA_VERSION=3 일 때만 사용, schema_v3.py)
APPLY_TYPES_V3 = APPLY_TYPES + ("HYBRID",)      # HYBRID = 일부 유형·단계는 학교 경유, 일부는 직접 신청
GPA_BASIS_V3 = GPA_BASIS + ("LAST_N", "ENTRANCE")   # LAST_N = 직전 N개 학기 평균(gpa.terms), ENTRANCE = 입학성적(신입생 전용)
MAX_VARIANTS = 12

# AI 에게 보여주는 출력 형식 (키 순서/기본값의 기준)
TEMPLATE: Dict[str, Any] = {
    "category": "OUT",
    "scholarship_name": None,
    "foundation": None,
    "apply_type": "UNKNOWN",
    "apply_start": None,
    "apply_end": None,
    "org_deadline": None,
    "recruit_count": None,
    "amount": None,
    "apply_how": None,
    "target_levels": [],
    "grades": [],
    "enrollment": {"required_status": [], "excluded_status": [], "min_remaining_semesters": None, "notes": None},
    "gpa": {"min": None, "scale": None, "basis": "UNKNOWN", "alternatives": [], "notes": None},
    "credits": {"min_last_semester": None, "min_completed_semesters": None, "notes": None},
    "income": {"max_bracket": None, "eligible_types": [], "notes": None},
    "region": {"residence": [], "school_location": [], "notes": None},
    "majors": [],
    "nationality": None,
    "special_status": [],
    "other_conditions": [],
    "variants": [],
    "exclusions": [],
    "documents": [],
    "selection": {"method": None, "priority": []},
    "summary": "",
    "evidence": {},
    "uncertain": [],
}


def empty_result() -> Dict[str, Any]:
    return copy.deepcopy(TEMPLATE)


# ---------------------------------------------------------------- 타입 정리
def _str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


_NAME_KEYS = ("name", "title", "label", "text", "value")
_NOTE_KEYS = ("notes", "note", "desc", "description", "reason")


def _item_text(x) -> Optional[str]:
    """목록 항목 하나를 문자열로. AI 가 {"name": ..., "notes": ...} 같은 객체로 쓴 경우 '이름 (메모)' 로 풀고,
    과거에 dict 의 repr 문자열("{'name': ...}")로 저장된 값도 복구한다."""
    if x is None:
        return None
    if isinstance(x, str):
        t = x.strip()
        if t.startswith("{") and t.endswith("}") and ("'name'" in t or '"name"' in t):
            try:
                parsed = ast.literal_eval(t)
                if isinstance(parsed, dict):
                    return _item_text(parsed)
            except (ValueError, SyntaxError):
                pass
        return t or None
    if isinstance(x, dict):
        name = next((_str(x.get(k)) for k in _NAME_KEYS if _str(x.get(k))), None)
        note = next((_str(x.get(k)) for k in _NOTE_KEYS if _str(x.get(k))), None)
        if name:
            return "{} ({})".format(name, note) if note else name
        vals = [str(v).strip() for v in x.values() if v not in (None, "", [], {})]
        return " / ".join(vals) or None
    if isinstance(x, (list, tuple)):
        return ", ".join(t for t in (_item_text(i) for i in x) if t) or None
    return str(x).strip() or None


def _strs(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (str, dict)):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    return [t for t in (_item_text(x) for x in v) if t]


def _num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _date(v) -> Tuple[Optional[str], bool]:
    """(YYYY-MM-DD 또는 None, 형식오류 여부)"""
    s = _str(v)
    if s is None:
        return None, False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s, False
    except ValueError:
        return None, True


def _to_date(s: Optional[str]) -> Optional[date]:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


# ---------------------------------------------------------------- 요일 검증
_DATE_WD = re.compile(r"(?:(\d{4})\s*[.\-/년]\s*)?(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*[.일]?\s*\(\s*([월화수목금토일])\s*\)")


def weekday_issues(text: str, posted: Optional[date], limit: int = 5) -> List[Dict[str, str]]:
    """원문 자체의 날짜-요일 불일치 탐지 (예: '9. 23.(금)' 인데 실제 수요일)."""
    out, seen = [], set()
    for m in _DATE_WD.finditer(text or ""):
        y, mo, d, w = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        if y:
            year = int(y)
        elif posted:
            year = posted.year
            if mo < posted.month - 6:
                year += 1
            elif mo > posted.month + 6:
                year -= 1
        else:
            continue
        try:
            actual = WEEKDAYS[date(year, mo, d).weekday()]
        except ValueError:
            continue
        if actual != w:
            key = m.group(0).strip()
            if key in seen:
                continue
            seen.add(key)
            out.append({"field": "source_text",
                        "reason": "원문 요일 불일치: '{}' (실제 {}요일)".format(key, actual)})
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- 정규화 + 검증

def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _norm_gpa(gp, issue, prefix: str, v3: bool = False) -> Dict[str, Any]:
    gp = _d(gp)
    basis = _str(gp.get("basis"))
    alts = []
    raw_alts = gp.get("alternatives")
    for a in (raw_alts if isinstance(raw_alts, list) else []):
        if isinstance(a, dict) and _num(a.get("min")) is not None:
            alts.append({"min": _num(a.get("min")), "scale": _num(a.get("scale"))})
    g = {"min": _num(gp.get("min")), "scale": _num(gp.get("scale")),
         "basis": basis if basis in (GPA_BASIS_V3 if v3 else GPA_BASIS) else "UNKNOWN", "alternatives": alts, "notes": _str(gp.get("notes"))}
    if g["min"] is not None:
        if g["scale"] is None:
            if not v3:      # v3: 만점 미기재는 검수 사유가 아니라 '4.5 가정' 대상 (scale_stated=false 로 표시)
                issue(prefix + ".scale", "성적 기준값은 있으나 만점 단위(4.5/4.3/100 등)가 없음")
        elif g["scale"] not in KNOWN_SCALES:
            issue(prefix + ".scale", "일반적이지 않은 만점 단위: {}".format(g["scale"]))
        elif g["min"] > g["scale"]:
            issue(prefix + ".min", "성적 기준값({})이 만점({})보다 큼".format(g["min"], g["scale"]))
    if v3:
        from . import schema_v3
        schema_v3.gpa_extra(gp, g, issue, prefix)
    return g


def _norm_credits(cr) -> Dict[str, Any]:
    cr = _d(cr)
    return {"min_last_semester": _num(cr.get("min_last_semester")),
            "min_completed_semesters": _num(cr.get("min_completed_semesters")), "notes": _str(cr.get("notes"))}


def _norm_income(inc, v3: bool = False) -> Dict[str, Any]:
    inc = _d(inc)
    out = {"max_bracket": _num(inc.get("max_bracket")), "eligible_types": _strs(inc.get("eligible_types")),
           "notes": _str(inc.get("notes"))}
    if v3:
        from . import schema_v3
        out.update(schema_v3.income_extra(inc))
    return out


def _norm_region(rg, v3: bool = False, issue=None, prefix: str = "region", sink=None) -> Dict[str, Any]:
    rg = _d(rg)
    out = {"residence": _strs(rg.get("residence")), "school_location": _strs(rg.get("school_location")),
           "notes": _str(rg.get("notes"))}
    if v3:
        from . import schema_v3
        out.update(schema_v3.region_extra(rg, issue or (lambda f, r: None), prefix, sink))
    return out


def _norm_variants(raw, top_gpa: Dict[str, Any], top_levels: List[str], issue, v3: bool = False) -> List[Dict[str, Any]]:
    """유형별 조건. 최상위 값 = 모든 유형 공통, 유형의 값 = 공통에 '추가로' 요구되는 조건(null/빈 값 = 추가 조건 없음).
    유형의 target_levels 가 비어 있으면 최상위와 같다는 뜻이다."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, it in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(it, dict):
            continue
        name = _str(it.get("name"))
        if not name:
            issue("variants[{}]".format(i), "유형 이름이 없어 제외")
            continue
        key = re.sub(r"\s+", "", name).lower()
        if key in seen:
            issue("variants[{}]".format(i), "같은 이름의 유형이 중복되어 제외: {}".format(name))
            continue
        if len(out) >= MAX_VARIANTS:
            issue("variants", "유형이 {}개를 넘어 앞 {}개만 저장".format(MAX_VARIANTS, MAX_VARIANTS))
            break
        seen.add(key)
        pfx = "variants[{}]".format(len(out))
        raw_levels = _strs(it.get("target_levels"))
        v_levels = [x for x in raw_levels if x in LEVELS]
        if len(v_levels) != len(raw_levels):
            issue(pfx + ".target_levels", "알 수 없는 학교급 제거: {}".format([x for x in raw_levels if x not in LEVELS]))
        extra = [x for x in v_levels if top_levels and x not in top_levels]
        if extra:
            issue(pfx + ".target_levels", "유형 '{}'의 학교급 {} 이(가) 최상위 target_levels 에 없음 - 최상위는 모든 유형의 합이어야 함".format(name, extra))
        v = {"name": name, "amount": _str(it.get("amount")), "target_levels": v_levels,
             "gpa": _norm_gpa(it.get("gpa"), issue, pfx + ".gpa", v3),
             "credits": _norm_credits(it.get("credits")), "income": _norm_income(it.get("income"), v3),
             "region": _norm_region(it.get("region"), v3, issue, pfx + ".region"), "other_conditions": _strs(it.get("other_conditions"))}
        if v3:
            from . import schema_v3
            v.update(schema_v3.variant_extra(it, v, issue, pfx))
        tg, vg = top_gpa, v["gpa"]
        if (tg["min"] is not None and vg["min"] is not None and tg["scale"] == vg["scale"]
                and vg["min"] < tg["min"]):
            issue(pfx + ".gpa", "공통 성적 기준({})이 유형 '{}'의 기준({})보다 높음 - 최상위 값이 유형별 값을 섞은 것인지 확인".format(
                tg["min"], name, vg["min"]))
        out.append(v)
    if len(out) == 1:
        issue("variants", "유형이 1개뿐임 - 유형이 둘 이상일 때만 variants 를 쓰므로 최상위 항목에 적는 것이 맞는지 확인")
    return out


def normalize(obj: Any, posted: Optional[date] = None, source_text: str = "",
              version: int = SCHEMA_VERSION) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """AI 가 만든 dict 를 스키마에 맞게 정리하고 (정리된 dict, 코드가 발견한 이슈 목록)을 돌려준다.
    version=2(기본값)는 v2 동작 그대로이고, version=3 이면 항목 사전 기반 확장 조건(schema_v3)도 정리한다."""
    v3 = version >= SCHEMA_VERSION_V3
    if not isinstance(obj, dict):
        raise ValueError("JSON 최상위가 객체가 아님")
    out = empty_result()
    issues: List[Dict[str, str]] = []
    moved: List[Dict[str, str]] = []                  # v3: 표준 값이 아니어서 '사전에 없는 조건'으로 옮겨진 항목

    def issue(field, reason):
        issues.append({"field": field, "reason": reason})

    def sink(text, kind="ELIGIBILITY", section=""):
        moved.append({"text": text, "kind": kind, "section": section, "source": "AI 출력"})

    cat = _str(obj.get("category"))
    if cat in CATEGORIES:
        out["category"] = cat
    else:
        out["category"] = "ETC"
        issue("category", "알 수 없는 구분값: {!r}".format(cat))

    at = _str(obj.get("apply_type"))
    out["apply_type"] = at if at in (APPLY_TYPES_V3 if v3 else APPLY_TYPES) else "UNKNOWN"

    for k in ("scholarship_name", "foundation", "recruit_count", "amount", "nationality"):
        out[k] = _str(obj.get(k))

    for k in ("apply_start", "apply_end", "org_deadline"):
        val, bad = _date(obj.get(k))
        out[k] = val
        if bad:
            issue(k, "날짜 형식 오류: {!r} (YYYY-MM-DD 필요)".format(obj.get(k)))

    if v3:
        # 유형(variants)마다 마감이 달라 최상위가 비어 있으면, 가장 늦은 유형 마감을 최상위 마감으로 쓴다 (유형별 값은 그대로 보존).
        # 최상위 마감이 비면 '마감일을 찾지 못함' 이슈가 생기고 DB 의 마감 컬럼이 NULL 이라 추천 조회(마감 전 공고)에서 빠지기 때문이다.
        vd = [v for v in (obj.get("variants") or []) if isinstance(v, dict)]
        if not out["apply_end"]:
            ends = [d for d, bad in (_date(v.get("apply_end")) for v in vd) if d]
            if ends:
                out["apply_end"] = max(ends)
        if not out["apply_start"]:
            starts = [d for d, bad in (_date(v.get("apply_start")) for v in vd) if d]
            if starts:
                out["apply_start"] = min(starts)
    if v3:
        # 유형마다 마감이 다르면 AI 가 최상위 마감을 비워 두는 것이 정상이다. 마감이 지난 것으로 보여 추천에서 빠지지 않게
        # 가장 늦은 유형 마감을 최상위 마감으로, 가장 이른 유형 시작을 최상위 시작으로 채운다 (유형별 날짜는 그대로 유지).
        vs = [v for v in (obj.get("variants") or []) if isinstance(v, dict)]
        for key, pick in (("apply_end", max), ("apply_start", min)):
            if not out[key]:
                vals = [d for d, bad in (_date(v.get(key)) for v in vs) if d]
                if vals:
                    out[key] = pick(vals)
    s, e = _to_date(out["apply_start"]), _to_date(out["apply_end"])
    if s and e and s > e:
        issue("apply_start", "신청 시작일이 종료일보다 늦음")
    if e and posted and e < posted - timedelta(days=7):
        issue("apply_end", "마감일({})이 게시일({})보다 많이 과거임 - 연도 오류 가능".format(out["apply_end"], posted))
    if out["category"] == "OUT" and not out["apply_end"]:
        issue("apply_end", "교외 장학금인데 마감일을 찾지 못함")

    levels = _strs(obj.get("target_levels"))
    out["target_levels"] = [x for x in levels if x in LEVELS]
    if len(out["target_levels"]) != len(levels):
        issue("target_levels", "알 수 없는 학교급 제거: {}".format([x for x in levels if x not in LEVELS]))
    grades = []
    for g in (obj.get("grades") or []):
        n = _num(g)
        if n is not None and 1 <= n <= 6:
            grades.append(int(n))
    out["grades"] = sorted(set(grades))

    en = _d(obj.get("enrollment"))
    out["enrollment"] = {
        "required_status": _strs(en.get("required_status")),
        "excluded_status": _strs(en.get("excluded_status")),
        "min_remaining_semesters": _num(en.get("min_remaining_semesters")),
        "notes": _str(en.get("notes")),
    }

    out["gpa"] = _norm_gpa(obj.get("gpa"), issue, "gpa", v3)
    out["credits"] = _norm_credits(obj.get("credits"))
    out["income"] = _norm_income(obj.get("income"), v3)
    out["region"] = _norm_region(obj.get("region"), v3, issue, "region", sink)

    for k in ("majors", "special_status", "other_conditions", "exclusions", "documents"):
        out[k] = _strs(obj.get(k))
    out["apply_how"] = _str(obj.get("apply_how"))
    out["variants"] = _norm_variants(obj.get("variants"), out["gpa"], out["target_levels"], issue, v3)
    sel = _d(obj.get("selection"))
    out["selection"] = {"method": _str(sel.get("method")), "priority": _strs(sel.get("priority"))}
    out["summary"] = _str(obj.get("summary")) or ""

    ev = obj.get("evidence")
    out["evidence"] = {str(k): str(v) for k, v in ev.items()} if isinstance(ev, dict) else {}

    unc = []
    for u in (obj.get("uncertain") or []):
        if isinstance(u, dict):
            lvl = _str(u.get("level"))
            unc.append({"field": _str(u.get("field")) or "", "reason": _str(u.get("reason")) or "",
                        "level": lvl if lvl in UNCERTAIN_LEVELS else "verify"})   # 표시가 없으면 안전하게 확인 대상으로
        elif _str(u):
            unc.append({"field": "", "reason": _str(u), "level": "verify"})
    out["uncertain"] = unc

    if v3:
        from . import schema_v3
        schema_v3.apply_v3(obj, out, issue, moved)

    issues.extend(weekday_issues(source_text, posted))
    return out, issues
