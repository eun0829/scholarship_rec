# -*- coding: utf-8 -*-
"""[RECOMMEND] 추천 서비스: 후보 조회 -> 묶기 -> 판정 -> 경고/정렬 -> 결과(JSON 직렬화 가능한 dict).

호출:  Recommender(source, settings).recommend(profile_dict, today=None)
  source : list_candidates(today) 를 가진 객체 (OracleRepository / MemoryRepository / FileSource)
결과에는 이름·학번 같은 개인정보를 담지 않는다 (버려진 개인정보 '항목 이름'과 가정한 값만 알림).
판정 사유 문구에는 해당 학생의 평점·학점 같은 속성 값이 들어갈 수 있으므로 결과는 그 학생에게만 전달해야 한다.
"""
from __future__ import annotations

import shutil
from datetime import date
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.config import Settings
from ..common.registry import load_registry
from . import matcher
from .matcher import is_v2
from .dedupe import merge_candidates
from .followup import MAX_QUESTIONS, MAX_ROUNDS, build_follow_up
from .result_format import RESULT_VERSION                       # 결과 JSON 형식 버전 (정의와 호환 규칙은 result_format.py)
from .student_text import parse_student_text
from .table import render_table, truncate
from .matcher import CONDITIONAL, MATCH, NO_MATCH, UNKNOWN
from ..common.ai.schema_v3 import SCHOOL_PROGRAMS
from .profile import SCHOOL_TYPES, ProfileError, _PROGRAM_WORDS, _SCHOOL_WORDS, _code_list, _mapped, build_profile, reason

VERDICT_LABEL = {MATCH: "충족", CONDITIONAL: "조건부", NO_MATCH: "미달"}


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _warnings(c: Dict[str, Any], today: date, days_left: int, urgent_days: int) -> List[Dict[str, str]]:
    w: List[Dict[str, str]] = []
    elig = c.get("elig") or {}

    def add(code, msg):
        w.append({"code": code, "message": msg})

    if days_left == 0:
        add("LAST_DAY", "오늘 마감입니다")
    elif days_left <= urgent_days:
        add("URGENT", "마감까지 {}일 남았습니다".format(days_left))
    if c.get("apply_start") and c["apply_start"] > today:
        add("NOT_STARTED", "신청 시작 전입니다 (시작일 {})".format(c["apply_start"].isoformat()))
    how = elig.get("apply_how") or ""
    docs = " ".join(elig.get("documents") or [])
    if "추천서" in how or "추천서" in docs:
        add("LETTER", "추천서가 필요해 준비 기간이 걸릴 수 있습니다")
    if c.get("org_deadline") and c.get("apply_end") and c["org_deadline"] != c["apply_end"]:
        add("ORG_DEADLINE", "재단 마감일은 {} 입니다 (학생이 따를 마감은 {})".format(c["org_deadline"].isoformat(), c["apply_end"].isoformat()))
    if c.get("status") == "NEED_REVIEW":
        add("NEED_REVIEW", "담당자 검수 대기 중인 공고입니다. 신청 전 원문을 꼭 확인하세요")
    for u in (c.get("uncertain") or {}).get("uncertain") or []:
        if isinstance(u, dict) and u.get("level", "verify") == "verify":
            add("VERIFY", "확인 필요({}): {}".format(u.get("field") or "기타", u.get("reason") or ""))
    if not is_v2(elig):
        add("LEGACY", "구형식 추출 결과입니다. 유형별 조건이 구분되지 않았을 수 있으니 원문을 확인하세요")
    for m in c.get("merged_from") or []:
        note = "같은 장학의 이전 공고({})와 묶었습니다 - 최신 공고 기준".format(m.get("src_id"))
        if m.get("apply_end") and c.get("apply_end") and m["apply_end"] != c["apply_end"].isoformat():
            note += " (마감 {} -> {})".format(m["apply_end"], c["apply_end"].isoformat())
        add("MERGED", note)
    return w


_CLOSED_TITLE = re.compile(r"^\s*[\[\(【<]\s*(?:접수\s*|모집\s*|신청\s*)?(?:마감|종료)(?:\s*됨)?\s*[\]\)】>]")


def is_marked_closed(c: Dict[str, Any]) -> bool:
    """게시판 제목 앞에 '[마감]' 같은 표시가 붙은 공고인가. 학교가 마감 처리하면 신청 기간 안이어도 더는 받지 않는 경우가 있다."""
    return bool(_CLOSED_TITLE.match(c.get("title") or ""))


def notice_id(c: Dict[str, Any]) -> str:
    """공고를 가리키는 값 ('EXT02:9096'). 후속 질문의 resolves, 변화 목록(changes)이 이 값을 쓴다."""
    return "{}:{}".format(c.get("src_cd"), c.get("src_id"))


TIER_LABEL = {"HIGH": "지원 가능성 높음", "PARTIAL": "일부 확인 필요", "CHECK": "확인 필요", "NONE": "해당 없음"}


def best_variant_and_tier(ev: matcher.Evaluation) -> Tuple[Optional[Dict[str, Any]], str]:
    """가장 가까운 유형(충족 유형 우선, 없으면 조건부 중 확인 항목이 가장 적은 유형)과 추천 등급.
    HIGH: 충족 유형이 있고 공통 확인 항목이 없음 / PARTIAL: 충족 유형은 있으나 공통 확인 항목이 남음 / CHECK: 조건부 / NONE: 미달."""
    if ev.verdict == NO_MATCH:
        return None, "NONE"
    ms = list(ev.matched_variants)
    best = None
    if ms:
        best = sorted(ms, key=lambda v: (0 if v.verdict == MATCH else 1, len(v.to_confirm)))[0]
    info = {"name": best.name, "verdict": best.verdict, "verdict_label": VERDICT_LABEL[best.verdict]} if best else None
    if ev.verdict == MATCH and not ev.to_confirm and (best is None or best.verdict == MATCH):
        return info, "HIGH"
    if best is not None and best.verdict == MATCH:
        return info, "PARTIAL"
    return info, "CHECK"


def evaluation_fields(ev: matcher.Evaluation, today: Optional[date] = None) -> Dict[str, Any]:
    """판정 결과(Evaluation)에서 나오는 항목 필드. 추천 서비스와 시험 추출 결과 확인 도구가 같은 모양을 쓴다.
    today 를 주면 유형별 마감이 지난 유형에 expired 표시를 한다."""
    iso = today.isoformat() if today else None
    best, tier = best_variant_and_tier(ev)
    return {
        "verdict": ev.verdict, "verdict_label": VERDICT_LABEL[ev.verdict],
        "best_variant": best, "tier": tier, "tier_label": TIER_LABEL[tier],
        "to_confirm": ev.to_confirm,
        "matched_variants": [{"name": v.name, "verdict": v.verdict, "verdict_label": VERDICT_LABEL[v.verdict], "amount": v.amount,
                              "apply_start": v.apply_start, "apply_end": v.apply_end, "expired": bool(iso and v.apply_end and v.apply_end < iso),
                              "preferences": v.preferences, "to_confirm": v.to_confirm}
                             for v in ev.matched_variants],
        "unmatched_variants": [{"name": v.name, "reason": v.fails[0] if v.fails else ""} for v in ev.variants if v.verdict == NO_MATCH],
        "passed": ev.passed,
        "fail_reasons": ev.fail_reasons,
        "assumptions": ev.assumptions,
        # 판정(충족·조건부·미달)에 영향을 주지 않고 따로 표시하는 항목
        "prerequisites": ev.prerequisites, "preferences": ev.preferences, "school_programs": ev.school_programs, "notes": ev.notes,
    }


VERDICT_ORDER = {MATCH: 0, CONDITIONAL: 1, NO_MATCH: 2}


def diff_evaluations(before: Sequence[Any], after: Sequence[Any]):
    """답을 넣기 전·후의 판정 비교 -> (변화 목록, 요약). 판정(충족·조건부·미달)이 바뀐 공고만 목록에 넣고, 판정은 같지만 확인 필요가 줄어든 공고는 요약에만 센다."""
    old = {notice_id(c): ev for c, ev in before}
    changes: List[Dict[str, Any]] = []
    unchanged = reduced = 0
    for c, ev in after:
        prev = old.get(notice_id(c))
        if prev is None:
            continue
        if prev.verdict == ev.verdict:
            unchanged += 1
            reduced += 1 if len(ev.to_confirm) < len(prev.to_confirm) else 0
            continue
        reason = (ev.fail_reasons[0] if ev.fail_reasons else "") if ev.verdict == NO_MATCH else (
            "남은 확인 항목이 모두 풀렸습니다" if ev.verdict == MATCH else (ev.to_confirm[0].split(" (")[0] if ev.to_confirm else ""))
        changes.append({"id": notice_id(c), "src_cd": c.get("src_cd"), "src_id": c.get("src_id"), "title": c.get("title"), "url": c.get("origin_url"),
                        "from": prev.verdict, "from_label": VERDICT_LABEL[prev.verdict], "to": ev.verdict, "to_label": VERDICT_LABEL[ev.verdict], "reason": reason})
    changes.sort(key=lambda x: (VERDICT_ORDER[x["to"]], x["id"]))
    summary = {"changed": len(changes), "to_match": sum(1 for x in changes if x["to"] == MATCH), "to_conditional": sum(1 for x in changes if x["to"] == CONDITIONAL),
               "to_no_match": sum(1 for x in changes if x["to"] == NO_MATCH), "unchanged": unchanged, "reduced_confirms": reduced}
    return changes, summary


def _item(c: Dict[str, Any], ev: matcher.Evaluation, today: date, days_left: int, urgent_days: int) -> Dict[str, Any]:
    elig = c["elig"]
    d = {
        "id": notice_id(c), "src_cd": c.get("src_cd"), "src_id": c.get("src_id"), "title": c.get("title"),
        "scholarship_name": elig.get("scholarship_name"), "foundation": elig.get("foundation"),
        "url": c.get("origin_url"), "summary": c.get("summary") or elig.get("summary"),
        "apply_start": _iso(c.get("apply_start")), "apply_end": _iso(c.get("apply_end")), "days_left": days_left,
        "org_deadline": _iso(c.get("org_deadline")), "apply_how": elig.get("apply_how"), "amount": elig.get("amount"),
        "warnings": _warnings(c, today, days_left, urgent_days),
        "merged_from": c.get("merged_from") or [],
        "schema_version": 3 if matcher.is_v3(elig) else (2 if is_v2(elig) else 1), "status": c.get("status"),
    }
    d.update(evaluation_fields(ev, today))
    return d


def _unknown_count(it: Dict[str, Any]) -> int:
    best = min((len(v["to_confirm"]) for v in it["matched_variants"]), default=0)
    return len(it["to_confirm"]) + best


class Recommender:
    def __init__(self, source, settings: Optional[Settings] = None):
        self.source = source
        self.s = settings or Settings()

    def recommend(self, profile_data: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
        return self._assemble(*self._evaluate_all(profile_data, today))

    def _evaluate_all(self, profile_data: Dict[str, Any], today: Optional[date] = None):
        """프로필로 모든 후보 공고를 판정한다. 후속 질문과 변화 비교가 평가 객체(Evaluation)를 쓸 수 있게 결과 조립과 나눠 둔다."""
        today = today or date.today()
        built = build_profile(profile_data, self.s.default_gpa_scale)       # ProfileError 는 호출자에게 전달
        p = built.profile
        self._fill_school_defaults(p)
        raw = self.source.list_candidates(today)

        allowed = set(self.s.recommend_categories)
        cands = [c for c in raw if (c.get("category") is None or c.get("category") in allowed) and c.get("elig")]
        if not self.s.recommend_include_need_review:
            cands = [c for c in cands if c.get("status") != "NEED_REVIEW"]
        cands = [c for c in cands if c.get("apply_end") and (c["apply_end"] - today).days >= self.s.recommend_min_days_left]
        cands = [c for c in cands if not is_marked_closed(c)]                 # 제목에 [마감] 표시가 붙은 공고는 신청 기간이 남아 있어도 뺀다
        n_filtered = len(cands)
        if self.s.recommend_dedupe:
            cands = merge_candidates(cands)

        evals = [(c, matcher.evaluate(c["elig"], p, defer_keys=self._defer_keys(), regular_terms=self.s.recommend_regular_terms)) for c in cands]
        return today, built, p, raw, n_filtered, cands, evals

    def _assemble(self, today: date, built, p, raw, n_filtered, cands, evals) -> Dict[str, Any]:
        match: List[Dict[str, Any]] = []
        cond: List[Dict[str, Any]] = []
        excluded: List[Dict[str, Any]] = []
        for c, ev in evals:
            days_left = (c["apply_end"] - today).days
            it = _item(c, ev, today, days_left, self.s.recommend_urgent_days)
            if ev.verdict == MATCH:
                match.append(it)
            elif ev.verdict == CONDITIONAL:
                cond.append(it)
            else:
                excluded.append({"id": notice_id(c), "src_cd": c.get("src_cd"), "src_id": c.get("src_id"), "title": c.get("title"), "url": c.get("origin_url"),
                                 "reasons": ev.fail_reasons[:3]})
        match.sort(key=lambda x: (x["days_left"], x["src_id"] or ""))
        cond.sort(key=lambda x: (_unknown_count(x), x["days_left"], x["src_id"] or ""))
        k = self.s.recommend_max_results
        return {
            "result_version": RESULT_VERSION,
            "today": today.isoformat(),
            "profile_notes": {"dropped_sensitive_keys": built.dropped_sensitive, "ignored_keys": built.ignored,
                              "assumptions": built.assumptions},
            "counts": {"candidates": len(raw), "after_filter": n_filtered, "after_dedupe": len(cands),
                       "match": len(match), "conditional": len(cond), "no_match": len(excluded)},
            "recommended": match[:k], "conditional": cond[:k], "excluded": excluded,
        }


    # ------------------------------------------------------------------ 5단계: 후속 질문과 이어 답하기 (서버는 상태를 저장하지 않는다)
    def recommend_step(self, profile_data: Dict[str, Any], answers: Optional[Dict[str, Any]] = None, answers_text: Optional[str] = None,
                       declined: Sequence[str] = (), round_no: int = 1, today: Optional[date] = None,
                       max_questions: int = MAX_QUESTIONS, max_rounds: int = MAX_ROUNDS) -> Dict[str, Any]:
        """판정 + 후속 질문. 이어서 답할 때는 지금까지의 입력(profile_data)과 이번 답(answers: 프로필 JSON 키, answers_text: '- 라벨: 값' 줄)을 따로 보낸다.
        답을 합쳐 판정하고, 답을 넣기 전과 비교한 변화(changes)를 함께 돌려준다. declined: 학생이 건너뛴 항목 코드 (다시 묻지 않는다)."""
        base = dict(profile_data or {})
        build_profile(base, self.s.default_gpa_scale)                       # 지금까지의 입력이 잘못됐으면 여기서 ProfileError
        applied, warnings, text_declined = self._parse_answers(base, answers, answers_text)
        declined_all = list(dict.fromkeys(list(declined or ()) + text_declined))
        merged = dict(base)
        merged.update(applied)
        new_ctx = self._evaluate_all(merged, today)
        result = self._assemble(*new_ctx)
        _, _, p, _, _, _, evals = new_ctx
        entries = [{"id": notice_id(c), "title": c.get("title") or "", "checks": [x for x in ev.common + [y for v in ev.matched_variants for y in v.checks]]}
                   for c, ev in evals if ev.verdict != NO_MATCH]
        result["round"] = round_no
        result["answers"] = {"applied": sorted(applied), "warnings": warnings, "declined": declined_all}
        result["follow_up"] = build_follow_up(entries, p, load_registry(), declined_all, round_no, max_questions, max_rounds)
        if applied:
            result["changes"], result["change_summary"] = diff_evaluations(self._evaluate_all(base, today)[-1], evals)
        else:
            result["changes"], result["change_summary"] = [], diff_evaluations(evals, evals)[1]
        return result

    def _parse_answers(self, base: Dict[str, Any], answers: Optional[Dict[str, Any]], answers_text: Optional[str]):
        """답 -> (적용할 프로필 키·값, 경고, 건너뛴 항목 코드). 틀린 값 하나가 나머지 답을 버리지 않도록 키마다 따로 검증한다."""
        applied: Dict[str, Any] = {}
        warnings: List[str] = []
        declined: List[str] = []
        for k, v in (answers or {}).items():
            try:
                built = build_profile(dict(base, **{k: v}), self.s.default_gpa_scale)
            except ProfileError as e:
                warnings.append("{}: {}".format(k, reason(k, e)))
                continue
            if k in built.ignored or k in built.dropped_sensitive:
                warnings.append("{}: 받지 않는 항목입니다".format(k))
                continue
            applied[k] = v
        if answers_text:
            ps = parse_student_text(answers_text)
            applied.update(ps.data)
            warnings += list(ps.report.warnings)
            declined += list(ps.report.declined)
        return applied, warnings, declined

    def _defer_keys(self):
        keys = set(self.s.recommend_defer_keys or ())
        bad = sorted(keys - set(matcher.CHECK_KEYS))
        if bad:
            raise ProfileError("환경설정 오류: RECOMMEND_DEFER_KEYS 에 알 수 없는 항목 {} (사용 가능: {})".format(bad, ", ".join(matcher.CHECK_KEYS)))
        return keys

    def _fill_school_defaults(self, p) -> None:
        """학교 고정 속성(.env 의 RECOMMEND_SCHOOL_*)을 학생 프로필에 없는 항목에만 채운다."""
        if p.school_type is None and self.s.recommend_school_type:
            try:
                p.school_type = _mapped(self.s.recommend_school_type, _SCHOOL_WORDS, SCHOOL_TYPES, "RECOMMEND_SCHOOL_TYPE")
            except ProfileError as e:
                raise ProfileError("환경설정 오류: {}".format(e))
        if p.school_sido is None and self.s.recommend_school_sido:
            p.school_sido = self.s.recommend_school_sido
        if p.school_sigungu is None and self.s.recommend_school_sigungu:
            p.school_sigungu = self.s.recommend_school_sigungu
        if p.school_programs is None and self.s.recommend_school_programs:
            try:
                p.school_programs = _code_list(list(self.s.recommend_school_programs), SCHOOL_PROGRAMS, _PROGRAM_WORDS, "RECOMMEND_SCHOOL_PROGRAMS")
            except ProfileError as e:
                raise ProfileError("환경설정 오류: {}".format(e))


# ------------------------------------------------------------------ 사람이 읽는 출력 (시험/점검용)
PREREQ_STATUS = {"MET": "충족", "TODO": "신청 필요", "UNMET": "미충족", "UNKNOWN": "확인 필요"}
PROGRAM_STATUS = {"PARTICIPATES": "본교 참여", "NOT": "본교 미참여", "UNKNOWN": "본교 참여 여부 설정 없음"}
APPLIES_LABEL = {True: "해당", False: "해당 없음", None: "모름"}


def side_lines(prerequisites: List[Dict[str, Any]], preferences: List[Dict[str, Any]], school_programs: List[Dict[str, Any]], notes: List[Dict[str, str]],
               max_notes: int = 3) -> List[str]:
    """판정과 따로 표시하는 항목(선행 조건·우대 조건·학교 사업·확인 사항)의 출력 줄."""
    L: List[str] = []
    if prerequisites:
        L.append("선행 조건: " + " / ".join("{} ({})".format(x["label"], PREREQ_STATUS[x["status"]]) for x in prerequisites))
    if preferences:
        L.append("우대 조건: " + " / ".join("{} ({})".format(x["label"], APPLIES_LABEL[x["applies"]]) for x in preferences))
    if school_programs:
        L.append("학교 사업: " + " / ".join("{} ({})".format(x["label"], PROGRAM_STATUS[x["school"]]) for x in school_programs))
    if notes:
        L.append("확인 사항: " + "; ".join("{}: {}".format(n["label"], n["text"][:40]) for n in notes[:max_notes])
                 + (" 외 {}개 (--json 으로 전체 확인)".format(len(notes) - max_notes) if len(notes) > max_notes else ""))
    return L


def _variant_label(v: Dict[str, Any]) -> str:
    end = v.get("apply_end")
    return "{}({}{})".format(v["name"], v["verdict_label"], (", ~" + end[5:] + (" 마감 지남" if v.get("expired") else "")) if end else "")
def format_text(result: Dict[str, Any], show_excluded: bool = False) -> str:
    L: List[str] = []
    c = result["counts"]
    L.append("=== 장학금 추천 결과 (기준일 {}) ===".format(result["today"]))
    L.append("후보 {}건 -> 필터 {}건 -> 묶은 뒤 {}건 | 충족 {} / 조건부 {} / 미달 {}".format(
        c["candidates"], c["after_filter"], c["after_dedupe"], c["match"], c["conditional"], c["no_match"]))
    pn = result["profile_notes"]
    if pn["dropped_sensitive_keys"]:
        L.append("※ 개인정보로 보여 받지 않은 항목: {}".format(", ".join(pn["dropped_sensitive_keys"])))
    for a in pn["assumptions"]:
        L.append("※ 가정: " + a)

    def block(title: str, items: List[Dict[str, Any]]):
        L.append("")
        L.append("[{}] {}건".format(title, len(items)))
        for i, it in enumerate(items, 1):
            L.append("{:>2}. {} (글번호 {} | 마감 {} | D-{})".format(i, it["title"], it["src_id"], it["apply_end"], it["days_left"]))
            if it.get("summary"):
                L.append("    요약: " + it["summary"])
            if it["matched_variants"]:
                L.append("    해당 가능 유형: " + ", ".join(_variant_label(v) for v in it["matched_variants"]))
            if it["unmatched_variants"]:
                L.append("    미달 유형: " + ", ".join(v["name"] for v in it["unmatched_variants"]))
            lines = ["? " + m for m in it["to_confirm"]]
            for v in it["matched_variants"]:
                lines += ["? [{}] {}".format(v["name"], m) for m in v["to_confirm"]]
            for m in lines[:6]:
                L.append("    " + m)
            if len(lines) > 6:
                L.append("    ... 외 확인 항목 {}개 (--json 으로 전체 확인)".format(len(lines) - 6))
            prefs = list(it.get("preferences") or []) + [dict(x, label="{} - {}".format(v["name"], x["label"])) for v in it["matched_variants"] for x in v.get("preferences") or []]
            for line in side_lines(it.get("prerequisites") or [], prefs, it.get("school_programs") or [], it.get("notes") or []):
                L.append("    " + line)
            for a in it.get("assumptions") or []:
                L.append("    ※ 가정: " + a)
            for w in it["warnings"]:
                L.append("    ! " + w["message"])
            if it.get("url"):
                L.append("    원문: " + it["url"])

    block("충족", result["recommended"])
    block("조건부 (확인 필요)", result["conditional"])
    if show_excluded:
        L.append("")
        L.append("[미달] {}건".format(len(result["excluded"])))
        for e in result["excluded"]:
            L.append("  - {} (글번호 {}): {}".format(e["title"], e["src_id"], "; ".join(e["reasons"])))
    L += follow_up_blocks(result, "ascii")
    return "\n".join(L)


# ------------------------------------------------------------------ 표 출력
WARNING_LABEL = {"LAST_DAY": "오늘 마감", "URGENT": "마감 임박", "NOT_STARTED": "신청 시작 전", "LETTER": "추천서 필요", "ORG_DEADLINE": "재단 마감일 다름",
                 "NEED_REVIEW": "검수 대기"}
CHECK_KEY_LABEL = {"levels": "학교급", "grade": "학년", "enrollment": "학적", "gpa": "성적", "credits": "학점", "terms": "학기", "income": "소득", "region": "지역",
                   "nationality": "국적", "major": "학과", "major_group": "학과 계열", "special": "신분", "age": "나이", "gender": "성별", "entry": "입학년도",
                   "transfer": "편입", "school": "학교 종류", "other": "자격 문장", "exclusion": "제외", "pending": "소득 확장", "new_student": "신입생", "language": "어학"}
STATUS_LABEL = {"PASS": "충족", "FAIL": "미달", "UNKNOWN": "확인 필요"}


def _lines(items: List[str], empty: str = "-") -> str:
    return "\n".join(items) if items else empty


def _confirm_cell(it: Dict[str, Any]) -> str:
    msgs = list(it.get("to_confirm") or [])
    for v in it.get("matched_variants") or []:
        msgs += ["[{}] {}".format(v["name"], m) for m in v.get("to_confirm") or []]
    if not msgs:
        return "-"
    return "\n".join(["{}개".format(len(msgs))] + [truncate(m.split(" (")[0], 28) for m in msgs[:2]])


def _variant_cell(it: Dict[str, Any]) -> str:
    rows = [_variant_label(v) for v in it.get("matched_variants") or []]
    if it.get("unmatched_variants"):
        rows.append("미달 유형: " + ", ".join(v["name"] for v in it["unmatched_variants"]))
    return _lines(rows)


def _deadline_cell(it: Dict[str, Any]) -> str:
    end = it.get("apply_end")
    left = it.get("days_left")
    first = (end[5:] if left is None else ("{} (D-{})".format(end[5:], left) if left >= 0 else "{} (마감 지남)".format(end[5:]))) if end else "-"
    return "\n".join([first] + [WARNING_LABEL.get(w.get("code"), truncate(w.get("message", ""), 12)) for w in it.get("warnings") or []])


def _prereq_cell(it: Dict[str, Any]) -> str:
    return _lines(["{} ({})".format(x["label"], PREREQ_STATUS[x["status"]]) for x in it.get("prerequisites") or []])


def _pref_cell(it: Dict[str, Any]) -> str:
    rows = ["{} ({})".format(x["label"], APPLIES_LABEL[x["applies"]]) for x in it.get("preferences") or []]
    rows += ["{} - {} ({})".format(v["name"], x["label"], APPLIES_LABEL[x["applies"]]) for v in it.get("matched_variants") or [] for x in v.get("preferences") or []]
    return _lines(rows)


def _program_cell(it: Dict[str, Any]) -> str:
    return _lines(["{} ({})".format(x["label"], PROGRAM_STATUS[x["school"]]) for x in it.get("school_programs") or []])


def _notes_cell(it: Dict[str, Any], max_notes: int = 3) -> str:
    ns = it.get("notes") or []
    rows = ["가정: " + a for a in it.get("assumptions") or []]
    rows += ["{}: {}".format(n["label"], truncate(n["text"], 40)) for n in ns[:max_notes]]
    if len(ns) > max_notes:
        rows.append("외 {}개 (--json)".format(len(ns) - max_notes))
    return _lines(rows)


def _title_cell(it: Dict[str, Any]) -> str:
    return it.get("title") or it.get("scholarship_name") or "(제목 없음)"


def format_table(result: Dict[str, Any], show_excluded: bool = False, style: str = "ascii", width: Optional[int] = None,
                 header: Optional[List[str]] = None) -> str:
    """추천 결과를 표로 출력한다. 충족·조건부는 '요약'과 '선행·우대·학교 사업·확인 사항' 표로, 끝에 원문 링크 표를 붙인다.
    style: ascii(터미널) / md(마크다운). header 를 주면 기본 머리말 대신 쓴다."""
    width = max(60, width or shutil.get_terminal_size((120, 24)).columns)
    c = result["counts"]
    L: List[str] = header[:] if header else [
        "=== 장학금 추천 결과 (기준일 {}) ===".format(result["today"]),
        "후보 {}건 -> 필터 {}건 -> 묶은 뒤 {}건 | 충족 {} / 조건부 {} / 미달 {}".format(c["candidates"], c["after_filter"], c["after_dedupe"], c["match"], c["conditional"], c["no_match"])]
    pn = result.get("profile_notes") or {}
    if pn.get("dropped_sensitive_keys"):
        L.append("※ 개인정보로 보여 받지 않은 항목: {}".format(", ".join(pn["dropped_sensitive_keys"])))
    for a in pn.get("assumptions") or []:
        L.append("※ 가정: " + a)
    links: List[List[str]] = []

    def section(title: str, items: List[Dict[str, Any]], verdict: str) -> None:
        L.append("")
        L.append("[{}] {}건".format(title, len(items)))
        if not items:
            return
        has_deadline = any(it.get("apply_end") or it.get("warnings") for it in items)       # 마감·경고가 하나도 없으면 열을 뺀다 (시험 추출 결과 확인 등)
        if style == "md":
            rows = []
            for i, it in enumerate(items, 1):
                t = _title_cell(it)
                rows.append([str(i), "[{}]({})".format(t, it["url"]) if it.get("url") else t] + ([_deadline_cell(it)] if has_deadline else [])
                            + [_variant_cell(it), _confirm_cell(it), _prereq_cell(it), _pref_cell(it), _program_cell(it), _notes_cell(it)])
            L.append(render_table(["#", "장학금"] + (["마감"] if has_deadline else []) + ["해당 가능 유형", "확인 필요", "선행 조건", "우대 조건", "학교 사업", "확인 사항"], rows, style="md"))
            return
        rows_a = [[str(i), str(it.get("src_id") or ""), _title_cell(it)] + ([_deadline_cell(it)] if has_deadline else []) + [_variant_cell(it), _confirm_cell(it)]
                  for i, it in enumerate(items, 1)]
        L.append(render_table(["#", "글번호", "장학금"] + (["마감"] if has_deadline else []) + ["해당 가능 유형", "확인 필요"], rows_a, max_width=width))
        rows_b = [[str(i), _prereq_cell(it), _pref_cell(it), _program_cell(it), _notes_cell(it)] for i, it in enumerate(items, 1)]
        if any(c != "-" for r in rows_b for c in r[1:]):                    # 내용이 하나도 없으면(v2 공고 등) 표를 생략
            L.append("")
            L.append("  선행 조건 · 우대 조건 · 학교 사업 · 확인 사항 (판정에는 영향 없음)")
            L.append(render_table(["#", "선행 조건", "우대 조건", "학교 사업", "확인 사항"], rows_b, max_width=width))
        links.extend([[VERDICT_LABEL[verdict], str(it.get("src_id") or ""), it.get("url") or "-"] for it in items])

    section("충족", result.get("recommended") or [], MATCH)
    section("조건부 (확인 필요)", result.get("conditional") or [], CONDITIONAL)
    if show_excluded:
        ex = result.get("excluded") or []
        L.append("")
        L.append("[미달] {}건".format(len(ex)))
        if ex:
            if style == "md":
                L.append(render_table(["#", "장학금", "미달 사유"], [[str(i), "[{}]({})".format(e["title"], e["url"]) if e.get("url") else e["title"], "; ".join(e["reasons"])]
                                                                for i, e in enumerate(ex, 1)], style="md"))
            else:
                L.append(render_table(["#", "글번호", "장학금", "미달 사유"], [[str(i), str(e.get("src_id") or ""), e["title"] or "", "\n".join(e["reasons"])]
                                                                         for i, e in enumerate(ex, 1)], max_width=width))
                links.extend([[VERDICT_LABEL[NO_MATCH], str(e.get("src_id") or ""), e.get("url") or "-"] for e in ex])
    if style != "md" and any(r[2] != "-" for r in links):
        L.append("")
        L.append("[원문 링크]")
        L.append(render_table(["판정", "글번호", "원문"], links, max_width=width, flex=[True, True, False]))
    L += follow_up_blocks(result, style, width)
    return "\n".join(L)


DONE_LABEL = {"NO_QUESTIONS": "더 물을 것이 없습니다 (남은 확인 필요는 공고문에서 직접 확인해야 합니다)", "ALL_DECLINED": "건너뛴 항목만 남았습니다",
              "MAX_ROUNDS": "후속 질문은 최대 {max_rounds}회까지입니다"}


def follow_up_blocks(result: Dict[str, Any], style: str = "ascii", width: Optional[int] = None) -> List[str]:
    """답변으로 바뀐 판정(변화), 후속 질문, 학교 DB 누락, 공고문 확인 목록을 표로 만든다 (결과에 follow_up 이 있을 때만)."""
    fu = result.get("follow_up")
    if not fu:
        return []
    width = max(60, width or shutil.get_terminal_size((120, 24)).columns)
    L: List[str] = []
    ans = result.get("answers") or {}
    for w in ans.get("warnings") or []:
        L.append("※ 답변 경고: " + w)
    changes = result.get("changes") or []
    cs = result.get("change_summary") or {}
    if ans.get("applied"):
        L += ["", "[변화] 답을 넣기 전과 비교: 바뀐 공고 {} (충족 {} / 조건부 {} / 미달 {}), 판정은 같지만 확인 필요가 줄어든 공고 {}".format(
            cs.get("changed", 0), cs.get("to_match", 0), cs.get("to_conditional", 0), cs.get("to_no_match", 0), cs.get("reduced_confirms", 0))]
        if changes:
            L.append(render_table(["공고", "장학금", "이전", "이후", "이유"], [[c["id"].split(":")[-1], c["title"] or "", c["from_label"], c["to_label"], c["reason"]] for c in changes],
                                  max_width=width, style=style))
    L += ["", "[후속 질문] {}회차 / 최대 {}회".format(fu["round"], fu["max_rounds"])]
    if fu["questions"]:
        rows = [[str(i), q["label"], q["question"], str(q["resolves_count"]), "민감 (건너뛰어도 됩니다)" if q["sensitive"] else "일반", q["answer"].get("text", "")] for i, q in enumerate(fu["questions"], 1)]
        L.append(render_table(["#", "항목", "질문", "풀어 주는 공고", "구분", "답변 예시"], rows, max_width=width, style=style))
        if fu.get("remaining"):
            L.append("(다음 회차에 더 물을 항목 {}개)".format(fu["remaining"]))
    if fu["done"]:
        L.append("후속 질문 종료: " + DONE_LABEL[fu["done_reason"]].format(max_rounds=fu["max_rounds"]))
    if fu.get("data_gap"):
        L += ["", "[학교 DB에서 가져오지 못한 값] 학생에게 묻지 않습니다. 포털이 함께 보내 주면 더 많은 공고를 확정할 수 있습니다."]
        L.append(render_table(["항목", "JSON 키", "풀어 주는 공고"], [[g["label"], g["field"] or "", str(g["resolves_count"])] for g in fu["data_gap"]], max_width=width, style=style))
    docs = fu.get("document_only") or []
    if docs:
        L += ["", "[공고문에서 직접 확인할 것] 입력으로는 풀 수 없는 조건 ({}건)".format(len(docs))]
        L.append(render_table(["공고", "장학금", "확인할 내용"], [[d["id"].split(":")[-1], d["title"], "\n".join(d["items"])] for d in docs[:12]], max_width=width, style=style))
        if len(docs) > 12:
            L.append("(외 {}건 - --json 으로 전체 확인)".format(len(docs) - 12))
    return L


def detail_table(item: Dict[str, Any], ev: matcher.Evaluation, style: str = "ascii", width: Optional[int] = None) -> str:
    """공고 하나의 모든 판정 항목(공통·유형별)과 별도 표기를 한 표로 보여 준다."""
    width = max(60, width or shutil.get_terminal_size((120, 24)).columns)
    rows: List[List[str]] = []
    for ch in ev.common:
        rows.append(["공통", CHECK_KEY_LABEL.get(ch.key, ch.key), STATUS_LABEL[ch.status], ch.message])
    for v in ev.variants:
        end = " (~{})".format(v.apply_end[5:]) if v.apply_end else ""
        rows.append(["유형: " + v.name, "판정", VERDICT_LABEL[v.verdict] + end, ""])
        for ch in v.checks:
            rows.append(["유형: " + v.name, CHECK_KEY_LABEL.get(ch.key, ch.key), STATUS_LABEL[ch.status], ch.message])
    for x in item.get("prerequisites") or []:
        rows.append(["선행 조건", "-", PREREQ_STATUS[x["status"]], x["label"] + (": " + x["text"] if x.get("text") else "")])
    for x in item.get("preferences") or []:
        rows.append(["우대 조건", "-", APPLIES_LABEL[x["applies"]], x["label"]])
    for x in item.get("school_programs") or []:
        rows.append(["학교 사업", "-", PROGRAM_STATUS[x["school"]], x["label"]])
    for n in item.get("notes") or []:
        rows.append(["확인 사항", "-", "-", "{}: {}".format(n["label"], n["text"])])
    for a in item.get("assumptions") or []:
        rows.append(["가정", "-", "-", a])
    title = "{} {} {}".format("[" + VERDICT_LABEL[item["verdict"]] + "]", item.get("src_id") or "", _title_cell(item))
    out = title + "\n" + render_table(["구분", "항목", "상태", "내용"], rows, max_width=width, style=style)
    if item.get("url"):
        out += "\n원문: " + item["url"]                                     # 주소는 표 안에서 줄바꿈되면 열 수 없어 표 아래에 한 줄로
    return out
