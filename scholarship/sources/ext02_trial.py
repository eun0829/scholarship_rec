# -*- coding: utf-8 -*-
"""[EXT02] 스키마 v3 시험 추출: 조사한 공고 본문으로 v3 프롬프트를 시험하고, 사람이 읽고 분류한 기대 결과와 항목별로 대조한다.

판단하려는 것
  1) AI 가 항목 사전의 조건을 맞게 채우는가 (재현율)
  2) 사전에 없거나 못 채운 조건이 '사전에 없는 조건(unmodeled_conditions)'과 메모에 원문으로 남는가, 아니면 사라지는가 (정보 손실)
  3) 응답이 잘리거나(토큰), 검수 사유가 얼마나 생기는가
DB 에는 아무것도 저장하지 않는다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# 시험 표본 20건: 앞쪽 5건은 길고 까다로운 것(유형별 마감, 면제, 지역 대안, 우대 신분)이라 1차에서 응답 길이와 형식을 먼저 확인한다.
DEFAULT_SAMPLE = ["9195", "9211", "9096", "9098", "9212",                        # 1차(5건)
                  "9237", "9120", "9217", "9210", "9178", "9181", "9066",        # 지역·학교 경유·학기 조건
                  "9088", "9163", "9144", "9058", "9053", "9141", "9227", "9071"]  # 소득·성적 표기·해외·직업 기준

# 구조 항목이 없는 조건(원문이 남는지만 확인한다)
UNDETECTABLE = {"VAR", "NODATA", "ACT", "HSG", "DONG", "TUITION", "SL"}
# 사전에 항목이 없는 조건의 원문 보존 확인용 키워드
EXTRA_HINTS = {
    "REC": ["추천"], "ROUTE": ["학교\\s*경유|소속\\s*대학|학교\\s*접수"], "OBL": ["수여식|봉사|의무|보고서|참석"], "CONC": ["중복|타\\s*(기관|장학|재단)|기\\s*수령|본인\\s*부담\\s*등록금|등록금.{0,6}(미만|초과)"],
    "PRIOR": ["기\\s*수혜|기\\s*수령|수혜자\\s*제외|이전|직전.*장학생|전년도|신규\\s*장학생|선발된"], "ESSAY": ["인재상|에세이|자기\\s*소개|서술|소개서"], "OVS": ["입학\\s*허가|교환\\s*학생|유학"],
    "INCM": ["중위\\s*소득"], "AST": ["재산"], "INCO": ["평균\\s*임금|월\\s*소득"], "INCT": ["경제적|가정\\s*형편|어려|저소득"], "PWORK": ["부모|자녀.*(근로|근무|선원)|(근로|근무|선원).*자녀"],
    "WCH": ["근로자\\s*자녀"], "REALRES": ["실거주|거주\\s*증빙|인근"], "GR": ["내신|수능|입학\\s*성적"], "DEG": ["학위|졸업\\s*\\(?예정"],
    "SCALE_UNSPEC": ["\\d\\.\\d"],
}
_SPECIAL = {"BASIC_LIVELIHOOD": "BASIC", "NEAR_POOR": "BASIC", "SINGLE_PARENT": "SPAR", "DISABLED": "DIS", "DEFECTOR": "DEF",
            "MULTICULTURAL": "MUL", "VETERAN": "VET", "MULTI_CHILD": "MCH", "WORKER_CHILD": "WCH"}
_REGION = {"RESIDENCE_SELF": "RES", "RESIDENCE_PARENT": "RESP", "GRADUATED_SCHOOL": "HSL", "SCHOOL_LOCATION": "SLOC",
           "PARENT_WORKPLACE": "PWORK", "ACTUAL_RESIDENCE": "REALRES"}
_PREREQ = {"DEGREE_HELD": "DEG", "NATIONAL_SCHOLARSHIP_APPLIED": "NSA", "BRACKET_CALCULATED": "NSA", "ADMISSION_OFFER": "OVS",
           "EXCHANGE_SELECTED": "OVS"}


# ================================================================== 공고 파일 읽기
@dataclass
class TrialNotice:
    idx: str
    state: str
    title: str
    org: str
    body: str
    period_start: Optional[str] = None      # 신청기간 시작일(YYYY-MM-DD). 시험 추출에서 [기준일]로 씀 (지난 공고가 '마감 지남'으로 오해되지 않게)


_PERIOD = re.compile(r"신청기간 원문:\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")
_HEAD = re.compile(r"^### \[(\d+)\] idx=(\d+) \| ([^|]*) \| (.*) \| ([^|]*)$")


def parse_notice_file(text: str) -> List[TrialNotice]:
    """조사 도구가 만든 ext02_survey_notices.txt 를 공고별로 나눈다. 본문은 머리말 두 줄 다음부터."""
    out: List[TrialNotice] = []
    for block in re.split(r"(?m)^(?=### \[)", (text or "").replace("\r", "")):
        lines = block.split("\n")
        m = _HEAD.match(lines[0].strip()) if lines else None
        if not m:
            continue
        body = "\n".join(lines[2:]).strip()                # 1줄 머리말, 2줄 '신청기간 원문 | 목록 태그 | 첨부'
        p = _PERIOD.search(lines[1]) if len(lines) > 1 else None
        start = "{}-{:02d}-{:02d}".format(p.group(1), int(p.group(2)), int(p.group(3))) if p else None
        out.append(TrialNotice(idx=m.group(2), state=m.group(3).strip(), title=m.group(4).strip(), org=m.group(5).strip(), body=body,
                               period_start=start))
    return out


def dreamspon_view_url(idx: str, base: Optional[str] = None) -> str:
    """드림스폰 공고 상세 주소 (https://www.dreamspon.com/scholarship/view.html?idx=글번호). 시험 추출 결과에는 주소가 없어 글번호에서 만든다."""
    try:
        from .ext02 import Ext02Config
        cfg = Ext02Config()
        default_base, path = cfg.base_url, cfg.view_path
    except Exception:                                              # 수집 어댑터를 읽을 수 없어도 주소는 만든다
        default_base, path = "https://www.dreamspon.com", "/scholarship/view.html"
    return "{}{}?idx={}".format((base or default_base).rstrip("/"), path, idx)


def load_expected(path: Optional[str] = None) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:
    """(공고 idx -> 기대 조건 코드 집합, 코드 -> 항목 이름)"""
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "data" / "ext02_survey_expected.json"
    d = json.loads(p.read_text(encoding="utf-8-sig"))
    return {k: set(v) for k, v in d["notices"].items()}, dict(d["codes"])


# ================================================================== AI 결과에서 조건 항목 뽑기
def _blocks(elig: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    yield "", elig
    for i, v in enumerate(elig.get("variants") or []):
        if isinstance(v, dict):
            yield "variants[{}].".format(i), v


def detect_codes(elig: Dict[str, Any]) -> Dict[str, str]:
    """정규화된 v3 결과(최상위 + 유형별)에서 구조로 담긴 조건 항목을 코드로 뽑는다. {코드: 찾은 위치}"""
    found: Dict[str, str] = {}

    def add(code: str, where: str) -> None:
        found.setdefault(code, where)

    for pre, b in _blocks(elig):
        g = b.get("gpa") or {}
        if g.get("min") is not None:
            basis = g.get("basis")
            if basis in ("LAST_SEMESTER", "EITHER"):
                add("GL", pre + "gpa")
            if basis in ("CUMULATIVE", "EITHER"):
                add("GC", pre + "gpa")
            if basis == "LAST_N":
                add("GN", pre + "gpa")
            if basis == "ENTRANCE":
                add("GR", pre + "gpa")
            if g.get("scale") == 100:
                add("SP", pre + "gpa")
        if g.get("scale_stated") is False:
            add("SCALE_UNSPEC", pre + "gpa")
        c = b.get("credits") or {}
        if c.get("min_last_semester") is not None:
            add("CR", pre + "credits")
        if c.get("min_completed_semesters") is not None or b.get("completed_terms"):
            add("SEM", pre + "completed_terms")
        if b.get("remaining_terms"):
            add("SLEFT", pre + "remaining_terms")
        inc = b.get("income") or {}
        if inc.get("max_bracket") is not None:
            add("INC", pre + "income")
        if inc.get("basis") == "MEDIAN_PERCENT" or inc.get("max_median_percent") is not None:
            add("INCM", pre + "income")
        if inc.get("max_asset_krw") is not None:
            add("AST", pre + "income")
        if inc.get("basis") == "WAGE_REF":
            add("INCO", pre + "income")
        if inc.get("basis") == "NARRATIVE":
            add("INCT", pre + "income")
        rg = b.get("region") or {}
        for r in rg.get("requirements") or []:
            code = _REGION.get(r.get("type"))
            if code:
                add(code, pre + "region.requirements")
            if r.get("min_years") is not None:
                add("RESY", pre + "region.requirements")
        if rg.get("residence"):
            add("RES", pre + "region.residence")                     # v2 식 표기도 인정
        if rg.get("school_location"):
            add("SLOC", pre + "region.school_location")
        for s in b.get("special_groups") or []:
            code = _SPECIAL.get(s.get("code"))
            if code:
                add(code, pre + "special_groups")
        if b.get("nationality"):
            add("NAT", pre + "nationality")
        if b.get("gender"):
            add("GEN", pre + "gender")
        if b.get("age"):
            add("AGE", pre + "age")
        if b.get("major_groups"):
            add("MAJ", pre + "major_groups")
        if (b.get("transfer") or {}).get("mode"):
            add("TRF", pre + "transfer")
        if b.get("entry_years"):
            add("ENT", pre + "entry_years")
        if b.get("apply_type") in ("SCHOOL", "HYBRID"):
            add("ROUTE", pre + "apply_type")
    e = elig
    if e.get("grades"):
        add("YEAR", "grades")
    if "GRAD" in (e.get("target_levels") or []):
        add("GRAD", "target_levels")
    en = e.get("enrollment") or {}
    status = " ".join((en.get("required_status") or []) + (en.get("excluded_status") or []))      # 메모('복학생도 신청 가능')는 허용 문구라 조건이 아님
    if re.search("휴학|복학", status):
        add("LV", "enrollment")
    if re.search("졸업", " ".join(en.get("excluded_status") or [])):
        add("GRD", "enrollment")
    if en.get("min_remaining_semesters") is not None:
        add("SLEFT", "enrollment")
    if e.get("new_student_only"):
        add("ENT", "new_student_only")
    if e.get("majors"):
        add("MAJ", "majors")
    if e.get("nationality"):
        add("NAT", "nationality")
    if e.get("language_requirements"):
        add("LANG", "language_requirements")
    sc = e.get("school") or {}
    if sc.get("types") or sc.get("excluded_types"):
        add("SCH_TYPE", "school")
    if sc.get("programs"):
        add("SPART", "school.programs")
    for p in e.get("prerequisites") or []:
        code = _PREREQ.get(p.get("type"))
        if code:
            add(code, "prerequisites")
    for w in e.get("waivers") or []:                                     # 조건 면제(예: 신입·편입·장애인은 성적 미적용)도 구조로 담은 것
        when = w.get("when") or {}
        for st in when.get("student") or []:
            code = {"NEW_STUDENT": "ENT", "TRANSFER": "TRF", "RETURNING": "LV"}.get(st)
            if code:
                add(code, "waivers")
        for sp in when.get("special") or []:
            code = _SPECIAL.get(sp)
            if code:
                add(code, "waivers")
    rec = e.get("recommendation") or {}
    if rec.get("required") or rec.get("from"):
        add("REC", "recommendation")
    if e.get("obligations"):
        add("OBL", "obligations")
    if e.get("review_criteria"):
        add("ESSAY", "review_criteria")
    for x in e.get("exclusion_items") or []:
        if str(x.get("type", "")).startswith("CONCURRENT"):
            add("CONC", "exclusion_items")
        elif str(x.get("type", "")).startswith("PRIOR"):
            add("PRIOR", "exclusion_items")
    return found


def preserved_texts(elig: Dict[str, Any]) -> List[str]:
    """구조 항목에 못 담은 조건이 원문으로 남아 있을 수 있는 모든 곳의 문장 (요약문은 제외)."""
    texts: List[str] = [u.get("text", "") for u in elig.get("unmodeled_conditions") or []]
    for k in ("other_conditions", "exclusions", "obligations", "review_criteria", "special_status", "majors", "documents"):
        texts += [str(x) for x in elig.get(k) or []]
    for k in ("enrollment", "gpa", "credits", "income", "region"):
        n = (elig.get(k) or {}).get("notes")
        if n:
            texts.append(str(n))
    texts += [str((elig.get("selection") or {}).get("method") or ""), str(elig.get("apply_how") or "")]
    for v in elig.get("variants") or []:
        texts += [str(x) for x in v.get("other_conditions") or []]
        if v.get("name"):
            texts.append(str(v["name"]))                                 # 유형 이름도 원문(예: '다자녀유형'). 단, 구조로 담긴 것은 아님
    return [t for t in texts if t]


def hint_patterns(code: str, registry=None) -> List["re.Pattern"]:
    hints: List[str] = list(EXTRA_HINTS.get(code, []))
    item = registry.get(code) if registry is not None else None
    if item is not None:
        hints += list(item.detect_hints)
    return [re.compile(h, re.I) for h in hints]


# ================================================================== 한 건 대조
def compare_one(expected: Set[str], elig: Dict[str, Any], registry=None) -> Dict[str, Any]:
    """기대 코드와 AI 결과를 대조한다.
    hit=구조로 찾음 / preserved=구조로는 못 찾았지만 원문이 남아 있음 / lost=구조에도 원문에도 없음(정보 손실 의심) /
    extra=기대에 없는데 AI 가 구조로 담음(AI 가 맞을 수도, 분류가 틀렸을 수도 있어 사람이 확인) / undetectable=구조 항목이 없는 조건"""
    found = detect_codes(elig)
    texts = preserved_texts(elig)
    comparable = {c for c in expected if c not in UNDETECTABLE}
    hit = sorted(c for c in comparable if c in found)
    missed = sorted(c for c in comparable if c not in found)
    preserved, lost, evidence = [], [], {}
    for c in missed:
        pats = hint_patterns(c, registry)
        text = next((t for t in texts if any(p.search(t) for p in pats)), None) if pats else None
        if text is not None:
            preserved.append(c)
            evidence[c] = text[:70]                                      # 어떤 문장이 근거인지 보고서에 보여 줌
        else:
            lost.append(c)
    extra = sorted(c for c in found if c not in expected and c not in UNDETECTABLE)
    context = {"variants": [str(v.get("name")) for v in elig.get("variants") or [] if v.get("name")],
               "unmodeled": [u.get("text", "")[:60] for u in elig.get("unmodeled_conditions") or []],
               "waivers": len(elig.get("waivers") or [])}
    return {"expected": sorted(expected), "hit": hit, "preserved": preserved, "lost": lost, "extra": extra,
            "undetectable": sorted(c for c in expected if c in UNDETECTABLE), "found": found,
            "preserved_evidence": evidence, "context": context}


# ================================================================== 크레딧 측정 (게이트웨이를 쓸 때)
def _flatten_numbers(d: Any, prefix: str = "") -> Dict[str, float]:
    out: Dict[str, float] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flatten_numbers(v, "{}{}.".format(prefix, k)))
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out[prefix[:-1]] = float(d)
    return out


def credit_snapshot(settings) -> Optional[Dict[str, float]]:
    """게이트웨이의 크레딧 조회 결과(숫자 항목 전부). 게이트웨이가 아니거나 조회에 실패하면 None."""
    from ..common.ai import gateway
    if not (settings.ai_base_url and gateway.is_gateway(settings.ai_base_url) and settings.ai_api_key):
        return None
    try:
        return _flatten_numbers(gateway.fetch_credits(settings.ai_base_url, settings.ai_api_key))
    except Exception:
        return None


def credit_diff(before: Optional[Dict[str, float]], after: Optional[Dict[str, float]]) -> List[str]:
    """실행 전후로 값이 바뀐 항목. (어느 항목이 '사용량'인지는 게이트웨이 응답 이름에 따라 다르므로 바뀐 것을 모두 보여준다)"""
    if not before or not after:
        return []
    return ["{}: {} -> {} ({:+.2f})".format(k, round(before[k], 2), round(after[k], 2), after[k] - before[k])
            for k in sorted(after) if k in before and abs(after[k] - before[k]) > 1e-9]


# ================================================================== 보고서
def _tier_label(code: str, registry) -> str:
    item = registry.get(code) if registry is not None else None
    return {1: "필수", 2: "권장", 3: "선택"}.get(item.tier, "") if item else "절차·기타"


def summarize(results: List[Dict[str, Any]], names: Dict[str, str], registry=None, credits: Optional[List[str]] = None) -> str:
    """results: 공고별 {idx, title, ok, error, issues, uncertain_verify, input_tokens, output_tokens, attempts, compare, unmodeled, elapsed_sec ...}"""
    L: List[str] = []
    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    L.append("=== 스키마 v3 시험 추출 보고서 ({}건 실행, 성공 {}건, 실패 {}건) ===".format(len(results), len(ok), len(bad)))
    if bad:
        L.append("실패: " + "; ".join("{} ({})".format(r["idx"], str(r.get("error"))[:60]) for r in bad))
    if credits:
        L.append("크레딧 변화(실행 전 -> 후):")
        L += ["  " + c for c in credits]
    if ok:
        tin = [r.get("input_tokens", 0) for r in ok]
        tout = [r.get("output_tokens", 0) for r in ok]
        L.append("토큰: 입력 평균 {:,.0f} / 최대 {:,}, 출력 평균 {:,.0f} / 최대 {:,} | 응답 잘림으로 재시도한 공고 {}건 | 평균 소요 {:.0f}초".format(
            sum(tin) / len(tin), max(tin), sum(tout) / len(tout), max(tout), sum(1 for r in ok if r.get("attempts", 1) > 1),
            sum(r.get("elapsed_sec", 0) for r in ok) / len(ok)))

    # ---------------- 항목별 재현율
    per: Dict[str, Counter] = {}
    for r in ok:
        cmp = r["compare"]
        for key in ("hit", "preserved", "lost"):
            for c in cmp[key]:
                per.setdefault(c, Counter())[key] += 1
                per[c]["expected"] += 1
    L.append("")
    L.append("[1] 항목별 재현율 (기대 = 제가 읽고 분류한 조건. 구조로 찾음 / 원문만 남음 / 사라짐)")
    L.append("  {:<9}{:<5}{:<30}{:>5}{:>8}{:>8}{:>8}{:>8}".format("등급", "코드", "항목", "기대", "찾음", "원문만", "사라짐", "재현율"))
    rows = sorted(per.items(), key=lambda kv: ({"필수": 0, "권장": 1, "선택": 2}.get(_tier_label(kv[0], registry), 3), -kv[1]["expected"], kv[0]))
    for code, c in rows:
        L.append("  {:<9}{:<5}{:<30}{:>5}{:>8}{:>8}{:>8}{:>7.0f}%".format(
            _tier_label(code, registry), code, names.get(code, "")[:15], c["expected"], c["hit"], c["preserved"], c["lost"],
            100.0 * c["hit"] / c["expected"]))
    tot = Counter()
    by_tier: Dict[str, Counter] = {}
    for code, c in per.items():
        t = _tier_label(code, registry)
        by_tier.setdefault(t, Counter()).update(c)
        tot.update(c)
    L.append("")
    for t in ("필수", "권장", "선택", "절차·기타"):
        c = by_tier.get(t)
        if c:
            L.append("  {} 합계: 기대 {} / 찾음 {} ({:.0f}%) / 원문만 {} / 사라짐 {}".format(
                t, c["expected"], c["hit"], 100.0 * c["hit"] / c["expected"], c["preserved"], c["lost"]))
    if tot["expected"]:
        L.append("  전체: 기대 {} / 찾음 {} ({:.0f}%) / 원문만 {} / 사라짐 {}  <- '찾음+원문만' = {:.0f}% (조건이 어디에도 안 남은 것은 {:.0f}%)".format(
            tot["expected"], tot["hit"], 100.0 * tot["hit"] / tot["expected"], tot["preserved"], tot["lost"],
            100.0 * (tot["hit"] + tot["preserved"]) / tot["expected"], 100.0 * tot["lost"] / tot["expected"]))

    # ---------------- 공고별 놓친 항목
    L.append("")
    L.append("[2] 공고별: 사라진 것(정보 손실 의심) / 원문만 남은 것 / AI 가 추가로 구조에 담은 것")
    for r in ok:
        c = r["compare"]
        if c["lost"] or c["preserved"] or c["extra"]:
            L.append("  {} {}".format(r["idx"], r["title"][:34]))
            if c["lost"]:
                L.append("      사라짐: " + ", ".join("{}({})".format(x, names.get(x, "")[:10]) for x in c["lost"]))
                ctx = c.get("context") or {}
                L.append("        (AI 가 쓴 유형 이름: {} | 면제 {}개 | 기타 목록: {})".format(
                    ", ".join(ctx.get("variants") or []) or "없음", ctx.get("waivers", 0),
                    " / ".join('"{}"'.format(t[:30]) for t in ctx.get("unmodeled") or []) or "없음"))
            for x in c["preserved"]:
                L.append("      원문만: {}({}) <- \"{}\"".format(x, names.get(x, "")[:10], (c.get("preserved_evidence") or {}).get(x, "")))
            if c["extra"]:
                L.append("      추가로 찾음(확인 필요): " + ", ".join("{}({})".format(x, names.get(x, "")[:10]) for x in c["extra"]))
    if not any(r["compare"]["lost"] or r["compare"]["preserved"] or r["compare"]["extra"] for r in ok):
        L.append("  (모든 공고가 기대와 일치)")

    # ---------------- 품질 신호
    L.append("")
    L.append("[3] 품질 신호")
    issues = Counter()
    for r in ok:
        for i in r.get("issues", []):
            issues[re.sub(r"\[\d+\]", "[n]", i.get("field", ""))] += 1
    need_review = sum(1 for r in ok if r.get("issues") or r.get("uncertain_verify"))
    L.append("  검수 대기(NEED_REVIEW)가 될 공고: {}건 / {}건 (코드가 찾은 이슈 또는 AI 의 verify 표시)".format(need_review, len(ok)))
    if issues:
        L.append("  코드가 찾은 이슈: " + ", ".join("{} {}건".format(k, v) for k, v in issues.most_common()))
    for r in ok:
        for u in (r.get("uncertain_items") or [])[:3]:
            L.append("    {} AI 검수 표시 [{}] {}".format(r["idx"], u.get("field", ""), str(u.get("reason", ""))[:70]))
    shas = Counter(r.get("prompt_sha") or "(기록 없음)" for r in ok)
    L.append("  프롬프트 버전: " + ", ".join("{} {}건".format(k, v) for k, v in shas.most_common()))
    um = [r.get("unmodeled", 0) for r in ok]
    if um:
        L.append("  사전에 없는 조건(unmodeled_conditions) 개수: 평균 {:.1f}, 최대 {}, 상한(20개)에 닿은 공고 {}건".format(
            sum(um) / len(um), max(um), sum(1 for x in um if x >= 20)))
    L.append("  region.mode 구분이 없어 UNKNOWN 인 공고: {}건 | 만점 미기재(scale_stated=false) 공고: {}건".format(
        sum(1 for r in ok if r.get("region_mode_unknown")), sum(1 for r in ok if "SCALE_UNSPEC" in r["compare"]["found"])))
    L.append("")
    L.append("[4] 판정 가이드 (제안 기준)")
    must = by_tier.get("필수", Counter())
    rec = by_tier.get("권장", Counter())
    mr = (must["hit"] + rec["hit"]) / max(1, must["expected"] + rec["expected"])
    lost_n = tot["lost"]
    L.append("  - 필수·권장 항목 재현율 {:.0f}% (기준: 80% 이상) -> {}".format(100 * mr, "충족" if mr >= 0.8 else "미달: 프롬프트 개선 필요"))
    L.append("  - 사라진 조건 {}건 (기준: 전체 기대의 5% 이하) -> {}".format(lost_n, "충족" if lost_n <= 0.05 * max(1, tot["expected"]) else "초과: 기타 목록 규칙 점검"))
    L.append("  - 응답 잘림 재시도 {}건, 실패 {}건 (기준: 둘 다 0~1건)".format(sum(1 for r in ok if r.get("attempts", 1) > 1), len(bad)))
    return "\n".join(L) + "\n"
