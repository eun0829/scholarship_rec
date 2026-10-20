# -*- coding: utf-8 -*-
"""[RECOMMEND] 정확도 자동 점검 (AI 호출 없음). 정답 규칙 없이 확인할 수 있는 것만 본다.

  1) 판정 불변 규칙: 성적이 오르면 결과가 나빠지지 않는다, 소득구간 숫자가 작아지면 나빠지지 않는다, 오류 없이 돌고 같은 입력은 같은 결과다
  2) 공고 분포: 어떤 학생도 충족하지 못하는 공고, 모든 학생이 조건부인 공고, 조건부로 남는 이유의 순위(고칠 목록)
  3) 추출 품질 검사: 유형 접두어가 남은 공통 문장, 시·도 없는 지역, 금액 빠짐, 구조로 옮기지 못한 숫자 조건 등
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import matcher as M
from . import matcher_v3 as V
from . import places
from .matrix import SCHOOLS, with_school
from .population import Student, region_probes
from .profile import StudentProfile

_NUM = re.compile(r"\d+(?:\.\d+)?")
RANK = {M.NO_MATCH: 0, M.CONDITIONAL: 1, M.MATCH: 2}
VERDICT_KO = {M.MATCH: "충족", M.CONDITIONAL: "조건부", M.NO_MATCH: "미달"}


# ================================================================== 공고 불러오기
def load_notices(folder: str) -> "Dict[str, Dict[str, Any]]":
    """export_notices.py 가 만든 폴더(*.json: {src_cd, src_id, title, ..., elig}) -> {키: 공고}. ELIG_JSON 의 schema_version 은 저장된 그대로 둔다."""
    out: Dict[str, Dict[str, Any]] = {}
    for f in sorted(Path(folder).glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(rec.get("elig"), dict):
            continue
        key = "{}:{}".format(rec.get("src_cd") or "?", rec.get("src_id") or f.stem)
        out[key] = rec
    return out


# ================================================================== 판정 실행
@dataclass
class Row:
    student: str
    notice: str
    verdict: str
    school: str = ""
    fails: List[str] = field(default_factory=list)
    unknown: List[Tuple[str, str, bool]] = field(default_factory=list)       # (check.key, message, 입력으로 풀 수 있는가)


def _unknowns(ev: M.Evaluation) -> List[Tuple[str, str, bool]]:
    out: List[Tuple[str, str, bool]] = []
    seen = set()
    checks = list(ev.common) + [c for v in ev.variants if v.verdict != M.NO_MATCH for c in v.checks]
    for c in checks:
        if c.status == M.UNKNOWN and (c.key, c.message) not in seen:
            seen.add((c.key, c.message))
            out.append((c.key, c.message, bool(c.needs)))
    return out


def evaluate_one(elig: Dict[str, Any], profile: StudentProfile) -> M.Evaluation:
    return M.evaluate(elig, profile)


def run_grid(notices: Dict[str, Dict[str, Any]], students: Sequence[Student], school: Optional[str] = None) -> Tuple[List[Row], List[Dict[str, str]]]:
    """school 이 None 이면 기본 학교 5곳 모두 (학교 소재지 조건 때문에 한 학교에서는 아무도 못 받는 공고를 가려내기 위해)."""
    rows: List[Row] = []
    crashes: List[Dict[str, str]] = []
    for sname in ([school] if school else list(SCHOOLS)):
        sc = SCHOOLS[sname]
        for st in students:
            p = with_school(st.profile, sc)
            for nk, n in notices.items():
                try:
                    ev = evaluate_one(n["elig"], p)
                except Exception as e:                                       # 한 쌍이 터져도 나머지는 계속 본다
                    crashes.append({"student": st.id, "notice": nk, "school": sname, "error": "{}: {}".format(type(e).__name__, e)})
                    continue
                rows.append(Row(st.id, nk, ev.verdict, sname, list(ev.fail_reasons), _unknowns(ev)))
    return rows, crashes


# ================================================================== 1) 불변 규칙
def _raised_gpa(p: StudentProfile) -> Optional[StudentProfile]:
    if p.gpa_last is None and p.gpa_cumulative is None:
        return None
    q = copy.deepcopy(p)
    scale = q.gpa_scale or 4.5
    for a in ("gpa_last", "gpa_cumulative"):
        v = getattr(q, a, None)
        if v is not None:
            setattr(q, a, min(scale, v + 0.5))
    for a in ("gpa_percent", "gpa_cumulative_percent"):
        v = getattr(q, a, None)
        if v is not None:
            setattr(q, a, min(100.0, v + 8))
    return q


def _lower_income(p: StudentProfile) -> Optional[StudentProfile]:
    if p.income_bracket is None or p.income_bracket <= 0:
        return None
    q = copy.deepcopy(p)
    q.income_bracket = max(0, p.income_bracket - 2)
    return q


def check_invariants(notices: Dict[str, Dict[str, Any]], students: Sequence[Student], school: str = "시흥", limit: int = 50) -> List[Dict[str, Any]]:
    """성적 상승·소득구간 하락이 결과를 나쁘게 하거나, 같은 입력의 결과가 달라지면 위반으로 모은다 (최대 limit 건)."""
    sc = SCHOOLS[school]
    out: List[Dict[str, Any]] = []
    for st in students:
        p = with_school(st.profile, sc)
        variants = (("gpa-up", _raised_gpa(p)), ("income-down", _lower_income(p)))
        for nk, n in notices.items():
            try:
                base = evaluate_one(n["elig"], p).verdict
                again = evaluate_one(n["elig"], p).verdict
            except Exception:
                continue                                                      # 오류는 run_grid 가 따로 보고
            if base != again:
                out.append({"rule": "determinism", "student": st.id, "notice": nk, "detail": "{} -> {}".format(base, again)})
            for rule, q in variants:
                if q is None:
                    continue
                try:
                    v = evaluate_one(n["elig"], q).verdict
                except Exception:
                    continue
                if RANK[v] < RANK[base]:
                    out.append({"rule": rule, "student": st.id, "notice": nk, "detail": "{} -> {}".format(VERDICT_KO[base], VERDICT_KO[v])})
            if len(out) >= limit:
                return out
    return out


# ================================================================== 2) 분포와 조건부 이유
def _top_fail(rows: Sequence[Row], nk: str) -> str:
    c: Counter = Counter()
    for r in rows:
        if r.notice == nk and r.verdict == M.NO_MATCH:
            for f in r.fails[:1]:
                c[_NUM.sub("#", f)[:70]] += 1
    return c.most_common(1)[0][0] if c else ""


def _top_blocker(rows: Sequence[Row], nk: str) -> str:
    """충족이 한 명도 없는 공고에서 조건부를 만드는 가장 흔한 이유 (이유 / 그것 하나만 풀면 충족이 되는 쌍 수)."""
    c: Counter = Counter()
    sole: Counter = Counter()
    for r in rows:
        if r.notice != nk or r.verdict != M.CONDITIONAL:
            continue
        kinds = {unknown_kind(k, m) for k, m, _ in r.unknown}
        for k in kinds:
            c[k] += 1
        if len(kinds) == 1:
            sole[next(iter(kinds))] += 1
    if not c:
        return ""
    k, n = c.most_common(1)[0]
    return "{} ({}쌍, 단독 {})".format(k, n, sole[k])


def distribution(rows: Sequence[Row], notices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    by_notice: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_notice[r.notice][r.verdict] += 1
    per: List[Dict[str, Any]] = []
    for nk in notices:
        c = by_notice.get(nk, Counter())
        total = sum(c.values())
        if not total:
            continue
        flag = ""
        if c[M.MATCH] == 0 and c[M.CONDITIONAL] == 0:
            flag = "ALL_NO"                                                   # 아무도 받을 수 없음: 조건이 잘못 추출됐을 가능성
        elif c[M.MATCH] == 0:
            flag = "NEVER_MATCH"                                              # 충족이 한 명도 없음: 판정 못 하는 조건이 남음
        per.append({"notice": nk, "title": notices[nk].get("title") or "", "total": total, "match": c[M.MATCH], "cond": c[M.CONDITIONAL],
                    "no": c[M.NO_MATCH], "flag": flag,
                    "top_fail": _top_fail(rows, nk) if flag == "ALL_NO" else _top_blocker(rows, nk) if flag else ""})
    overall = Counter(r.verdict for r in rows)
    return {"overall": {VERDICT_KO[k]: v for k, v in overall.items()}, "per_notice": per}


_SENTENCE_KEYS = ("other", "exclusion")                                 # 문장 자체가 내용인 확인 항목은 문장별로 센다


def unknown_kind(key: str, msg: str) -> str:
    head = _NUM.sub("#", msg if key in _SENTENCE_KEYS else msg.split(":")[0])[:60 if key in _SENTENCE_KEYS else 24]
    return "{}: {}".format(key, head)


def unknown_ranking(rows: Sequence[Row], top: int = 150) -> List[Dict[str, Any]]:
    """조건부로 남는 이유 순위. 같은 종류(check.key + 숫자를 지운 문장 앞부분)로 묶고 영향받은 (학생,공고) 쌍과 공고 수를 센다."""
    pairs: Counter = Counter()
    sole: Counter = Counter()
    notices: Dict[str, set] = defaultdict(set)
    sample: Dict[str, str] = {}
    solvable: Dict[str, int] = defaultdict(int)
    for r in rows:
        seen = set()
        for key, msg, can in r.unknown:
            k = unknown_kind(key, msg)
            if k in seen:
                continue
            seen.add(k)
            pairs[k] += 1
            notices[k].add(r.notice)
            sample.setdefault(k, msg)
            solvable[k] += 1 if can else 0
        if len(seen) == 1 and r.verdict == M.CONDITIONAL:
            sole[next(iter(seen))] += 1                                      # 이것 하나만 풀면 충족이 되는 쌍
    out = []
    for k, n in sorted(pairs.items(), key=lambda x: (-x[1], x[0]))[:top]:
        out.append({"kind": k, "pairs": n, "sole": sole[k], "notices": len(notices[k]), "sample": sample[k][:90],
                    "student_can_resolve": "예" if solvable[k] * 2 >= n else "아니오(데이터·추출 문제)"})
    return out


# ================================================================== 3) 추출 품질 검사
_STRUCTURABLE = re.compile(r"(평점|학점|소득|분위|구간|거주|주민등록|TOEIC|토익|TOEFL|IELTS|HSK|JLPT|어학|만\s*\d+\s*세|성별|국적)")
_MONEY = re.compile(r"\d[\d,]*\s*(?:만\s*원|원|만원)")


def _texts(elig: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = [("공통", t) for t in elig.get("other_conditions") or [] if isinstance(t, str)]
    out += [("공통", u.get("text", "")) for u in elig.get("unmodeled_conditions") or [] if isinstance(u, dict) and u.get("kind") == "ELIGIBILITY"]
    for v in elig.get("variants") or []:
        if isinstance(v, dict):
            out += [(v.get("name") or "유형", t) for t in v.get("other_conditions") or [] if isinstance(t, str)]
    return out


def lint_notice(rec: Dict[str, Any]) -> List[Dict[str, str]]:
    """공고 한 건의 추출 품질 문제. 코드(code)와 설명(detail)."""
    elig = rec["elig"]
    out: List[Dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        out.append({"code": code, "detail": detail[:140]})

    variants = [v for v in elig.get("variants") or [] if isinstance(v, dict)]
    names = {re.sub(r"\s+", "", v.get("name") or "") for v in variants}
    if (elig.get("schema_version") or 1) < 3:
        add("OLD_SCHEMA", "schema_version {} (v3 로 다시 추출하면 더 많은 조건을 구조로 얻음)".format(elig.get("schema_version") or 1))
    if len(variants) == 1:
        add("ONE_VARIANT", "유형이 1개뿐")
    for scope, t in _texts(elig):
        m = V._VARIANT_PREFIX.match(t)
        if m and names and V._prefix_matches(m.group(1), names) and scope == "공통":
            add("PREFIX_IN_COMMON", t)
        elif V.is_high_school_only(t):
            add("HIGH_SCHOOL_TEXT", t)                                        # 매칭에서는 빠지지만 저장 데이터에 남아 있음
        elif _STRUCTURABLE.search(t) and _NUM.search(t):
            add("STRUCTURABLE_TEXT", "[{}] {}".format(scope, t))
    blocks = [("공통", elig)] + [(v.get("name") or "유형", v) for v in variants]
    for scope, b in blocks:
        for rq in (b.get("region") or {}).get("requirements") or []:
            areas = [a for a in rq.get("areas") or [] if isinstance(a, str)]
            if not areas:
                add("REGION_NO_AREAS", "[{}] {}".format(scope, rq.get("type")))
            elif all((lambda pl: pl.sido is None and pl.city is None and not pl.group and not pl.foreign and not pl.domestic)(places.parse_place(a)) for a in areas):
                add("REGION_NO_SIDO", "[{}] {}".format(scope, ", ".join(areas[:4])))
        for sg in b.get("special_groups") or []:
            if isinstance(sg, dict) and sg.get("type") in ("OTHER", "ETC") and sg.get("mode") == "REQUIRED":
                add("SPECIAL_OTHER", "[{}] 필수 신분이 '기타': {}".format(scope, sg.get("text") or sg.get("label") or ""))
        g = b.get("gpa") or {}
        if g.get("min") is not None and g.get("scale") is None:
            add("GPA_NO_SCALE", "[{}] 평점 {} 의 만점이 없음".format(scope, g.get("min")))
        if scope != "공통" and not V._variant_has_conditions(b):
            add("VARIANT_EMPTY", "[{}] 유형에 구조 조건이 없음".format(scope))
    if not elig.get("amount") and not any(v.get("amount") for v in variants) and _MONEY.search(elig.get("summary") or rec.get("summary") or ""):
        add("AMOUNT_MISSING", elig.get("summary") or rec.get("summary") or "")
    if not rec.get("apply_end"):
        add("NO_DEADLINE", "마감일 없음")
    return out


def lint_all(notices: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    return {nk: l for nk, l in ((nk, lint_notice(n)) for nk, n in notices.items()) if l}


def lint_summary(lint: Dict[str, List[Dict[str, str]]]) -> List[Dict[str, Any]]:
    by_code: Dict[str, List[str]] = defaultdict(list)
    for nk, items in lint.items():
        for code in {i["code"] for i in items}:
            by_code[code].append(nk)
    return [{"code": c, "notices": len(v), "ids": sorted(v)} for c, v in sorted(by_code.items(), key=lambda x: -len(x[1]))]


# ================================================================== 4) 구조화 커버리지 (사전 확장 후보를 찾는 재료)
_STOP = {"이상", "이하", "이내", "해당", "경우", "대상", "조건", "필요", "확인", "자격", "또는", "그리고", "있는", "없는", "중", "및", "등", "자", "인", "자의", "본인", "학생", "공고문", "선발", "조건은", "or", "유형", "학년", "학기", "재학생", "대학생", "성적우수", "이며", "가능"}
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")


def _structured_count(b: Dict[str, Any]) -> int:
    n = 0
    g, c, inc, rg = b.get("gpa") or {}, b.get("credits") or {}, b.get("income") or {}, b.get("region") or {}
    n += bool(g.get("min") is not None or g.get("alternatives"))
    n += bool(c.get("min_last_semester") is not None or c.get("min_completed_semesters") is not None)
    n += bool(inc.get("max_bracket") is not None or inc.get("eligible_types") or inc.get("max_median_percent") is not None)
    n += len(rg.get("requirements") or []) or bool(rg.get("residence") or rg.get("school_location"))
    for k in ("grades", "target_levels", "majors", "major_groups", "nationality", "gender", "entry_years", "special_groups", "special_status", "language_requirements"):
        n += bool(b.get(k))
    for k in ("age", "completed_terms", "remaining_terms", "transfer"):
        n += any(x is not None for x in (b.get(k) or {}).values()) if isinstance(b.get(k), dict) else 0
    return int(n)


def coverage(notices: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """공고마다 구조로 담긴 조건 수와 문장으로만 남은 조건 수, 문장 조건에 자주 나오는 낱말(사전에 넣을 후보)."""
    per: List[Dict[str, Any]] = []
    words: Dict[str, set] = defaultdict(set)
    sentences: List[Dict[str, str]] = []
    for nk, n in notices.items():
        e = n["elig"]
        blocks = [e] + [v for v in e.get("variants") or [] if isinstance(v, dict)]
        st = sum(_structured_count(b) for b in blocks)
        texts = [(sc, t) for sc, t in _texts(e) if not V.is_high_school_only(t)]
        seen = set()
        uniq = []
        for sc, t in texts:                                                # 같은 문장이 공통과 유형에 반복되면 한 번만
            k = re.sub(r"\s+", "", t)
            if k not in seen:
                seen.add(k)
                uniq.append((sc, t))
        per.append({"notice": nk, "title": n.get("title") or "", "structured": st, "sentences": len(uniq),
                    "coverage": round(st / (st + len(uniq)), 3) if st + len(uniq) else 1.0})
        for sc, t in uniq:
            sentences.append({"notice": nk, "scope": sc, "text": t})
            for w in set(_WORD.findall(t)) - _STOP:
                words[w].add(nk)
    tot_s, tot_t = sum(p["structured"] for p in per), sum(p["sentences"] for p in per)
    top = sorted(((w, len(v)) for w, v in words.items() if len(v) >= 2), key=lambda x: (-x[1], x[0]))[:40]
    return {"overall": round(tot_s / (tot_s + tot_t), 3) if tot_s + tot_t else 1.0, "structured": tot_s, "sentences": tot_t,
            "per_notice": per, "top_words": [{"word": w, "notices": c} for w, c in top], "sentence_list": sentences}


# ================================================================== 보고서 재료
def build_report(notices: Dict[str, Dict[str, Any]], students: Sequence[Student], school: Optional[str] = None) -> Dict[str, Any]:
    students = list(students) + region_probes(notices)
    rows, crashes = run_grid(notices, students, school)
    lint = lint_all(notices)
    dist = distribution(rows, notices)
    n = len(rows)
    decided = dist["overall"].get("충족", 0) + dist["overall"].get("미달", 0)
    return {"school": school or "전체 {}곳".format(len(SCHOOLS)), "notices": len(notices), "students": len(students), "pairs": n,
            "decided_rate": round(decided / n, 4) if n else 0.0,
            "overall": dist["overall"], "per_notice": dist["per_notice"], "crashes": crashes,
            "invariants": check_invariants(notices, students, school or "시흥"),
            "unknown_ranking": unknown_ranking(rows), "lint": lint, "lint_summary": lint_summary(lint), "coverage": coverage(notices)}


def severe_count(rep: Dict[str, Any]) -> int:
    """종료 코드에 쓰는 심각한 문제 수: 오류 + 불변 규칙 위반. (아무도 못 받는 공고는 정당한 경우도 있어 검토 목록으로만 둔다)"""
    return len(rep["crashes"]) + len(rep["invariants"])


def diff_baseline(rep: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """기준선보다 새로 생긴 문제: 공고별 lint 코드, 플래그, 불변 위반 수."""
    def lint_set(r): return {(nk, i["code"]) for nk, items in r["lint"].items() for i in items}
    def flag_set(r): return {(p["notice"], p["flag"]) for p in r["per_notice"] if p["flag"]}
    return {"new_lint": sorted(lint_set(rep) - lint_set(base)), "fixed_lint": sorted(lint_set(base) - lint_set(rep)),
            "new_flags": sorted(flag_set(rep) - flag_set(base)), "fixed_flags": sorted(flag_set(base) - flag_set(rep)),
            "decided_rate": [base.get("decided_rate"), rep["decided_rate"]],
            "invariants": [len(base.get("invariants", [])), len(rep["invariants"])]}
