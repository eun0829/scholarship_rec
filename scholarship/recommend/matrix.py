# -*- coding: utf-8 -*-
"""[RECOMMEND] 4-B2 일치 시험: 가상 학생 × 공고 × 학교 조합을 엔진으로 판정하고 정답 규칙(matrix_truth)과 대조한다.

분류 (엔진 판정 vs 정답)
  OK                   일치
  FALSE_REJECT         엔진 미달 / 정답 미달 아님      (가장 심각: 받을 수 있는 학생을 탈락시킨다)
  FALSE_ACCEPT_MATCH   엔진 충족 / 정답 미달 확정      (심각: 대상이 아닌 학생에게 충족이라고 한다)
  OVERCONFIDENT        엔진 충족 / 정답 불확실        (확인할 조건이 남았는데 충족이라고 한다)
  FALSE_ACCEPT_COND    엔진 조건부 / 정답 미달 확정   (경미: 조건부로 보여 준다)
  KNOWN_LIMIT          위와 같지만 알려진 한계(읍·면·동 세부 지역 미판정, AI 구조화가 놓친 원문 조건)
  UNDERCONFIDENT       엔진 조건부 / 정답 충족        (정보: 충족이라고 해도 되는데 조건부로 둠)
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import matcher
from .matrix_truth import NO, RULES, UNSURE, YES, Facts, Truth, judge
from .profile import StudentProfile
from .student_text import profile_from_text

SCHOOLS: "OrderedDict[str, Dict[str, Any]]" = OrderedDict([
    ("시흥", {"sido": "경기도", "sigungu": "시흥시", "type": "GENERAL", "programs": ["NATIONAL_SCHOLARSHIP"]}),
    ("성북", {"sido": "서울특별시", "sigungu": "성북구", "type": "GENERAL", "programs": ["NATIONAL_SCHOLARSHIP"]}),
    ("해운대", {"sido": "부산광역시", "sigungu": "해운대구", "type": "GENERAL", "programs": ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"]}),
    ("유성", {"sido": "대전광역시", "sigungu": "유성구", "type": "GENERAL", "programs": ["NATIONAL_SCHOLARSHIP", "LOCAL_TALENT"]}),
    ("안산(전문대)", {"sido": "경기도", "sigungu": "안산시", "type": "COLLEGE", "programs": ["NATIONAL_SCHOLARSHIP"]}),
])
SEVERE = ("FALSE_REJECT", "FALSE_ACCEPT_MATCH", "OVERCONFIDENT")
ORDER = ("OK", "FALSE_REJECT", "FALSE_ACCEPT_MATCH", "OVERCONFIDENT", "FALSE_ACCEPT_COND", "KNOWN_LIMIT", "UNDERCONFIDENT")
VERDICT_KO = {matcher.MATCH: "충족", matcher.CONDITIONAL: "조건부", matcher.NO_MATCH: "미달"}
TRUTH_KO = {NO: "미달", UNSURE: "불확실", YES: "충족"}


@dataclass
class Student:
    id: str
    desc: str
    text: str
    profile: StudentProfile


@dataclass
class Pair:
    idx: str
    title: str
    student: Student
    school: str
    evaluation: matcher.Evaluation
    truth: Truth
    klass: str

    @property
    def verdict(self) -> str:
        return self.evaluation.verdict

    @property
    def key(self) -> str:
        return "{}:{}:{}".format(self.student.id, self.idx, self.school)


# ================================================================== 불러오기
def default_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "v3_matrix"


def load_notices(path: Optional[str] = None) -> "OrderedDict[str, Dict[str, Any]]":
    """공고 폴더(*.json: {idx, title, url, data} 또는 시험 추출 결과 {idx, title, ok, data}) -> {idx: {title, url, elig}}"""
    folder = Path(path) if path else default_dir() / "notices"
    out: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for f in sorted(folder.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("ok") is False or not rec.get("data"):
            continue
        out[str(rec["idx"])] = {"title": rec.get("title") or "", "url": rec.get("url"), "elig": dict(rec["data"], schema_version=3)}
    return out


def load_students(path: Optional[str] = None) -> List[Student]:
    text = Path(path).read_text(encoding="utf-8") if path else (default_dir() / "students.txt").read_text(encoding="utf-8")
    parts = re.split(r"^=== (s\d+) \| (.*?) ===$", text, flags=re.M)
    students: List[Student] = []
    for i in range(1, len(parts), 3):
        body = parts[i + 2]
        built, _ = profile_from_text(body)
        students.append(Student(parts[i], parts[i + 1], body, built.profile))
    return students


# ================================================================== 분류와 실행
def classify(verdict: str, truth: Truth) -> str:
    if verdict == matcher.NO_MATCH:
        return "OK" if truth.outcome == NO else "FALSE_REJECT"
    if truth.outcome == NO:
        if verdict == matcher.MATCH:
            return "FALSE_ACCEPT_MATCH"
        return "KNOWN_LIMIT" if truth.tags & {"detail-place", "ai-gap"} else "FALSE_ACCEPT_COND"
    if truth.outcome == UNSURE:
        return "OVERCONFIDENT" if verdict == matcher.MATCH else "OK"
    return "OK" if verdict == matcher.MATCH else "UNDERCONFIDENT"


def with_school(profile: StudentProfile, school: Dict[str, Any]) -> StudentProfile:
    p = copy.deepcopy(profile)
    p.school_sido, p.school_sigungu, p.school_type, p.school_programs = school["sido"], school["sigungu"], school["type"], list(school["programs"])
    return p


def run(notices: Dict[str, Dict[str, Any]], students: Sequence[Student], schools: Optional[Iterable[str]] = None) -> List[Pair]:
    names = list(schools) if schools else list(SCHOOLS)
    pairs: List[Pair] = []
    for sname in names:
        school = SCHOOLS[sname]
        for st in students:
            p = with_school(st.profile, school)
            facts = Facts(p, school)
            for idx, n in notices.items():
                if idx not in RULES:
                    continue
                ev = matcher.evaluate(n["elig"], p)
                t = judge(idx, facts)
                pairs.append(Pair(idx, n["title"], st, sname, ev, t, classify(ev.verdict, t)))
    return pairs


def counts(pairs: Sequence[Pair]) -> "Counter[str]":
    return Counter(p.klass for p in pairs)


def problems(pairs: Sequence[Pair], include_info: bool = False) -> List[Pair]:
    keep = set(ORDER) - {"OK"} if include_info else set(ORDER) - {"OK", "UNDERCONFIDENT", "KNOWN_LIMIT"}
    return [p for p in pairs if p.klass in keep]


def coverage(pairs: Sequence[Pair]) -> Dict[str, Counter]:
    """공고별 정답 분포 (공고마다 미달·불확실 학생이 모두 있어야 시험이 의미 있다)"""
    out: Dict[str, Counter] = {}
    for p in pairs:
        out.setdefault(p.idx, Counter())[p.truth.outcome] += 1
    return out


def engine_reasons(p: Pair, limit: int = 2) -> str:
    ev = p.evaluation
    if ev.verdict == matcher.NO_MATCH:
        return "; ".join(ev.fail_reasons[:limit])
    return "; ".join([m.split(" (")[0] for m in ev.to_confirm[:limit]]) or "-"


def truth_reasons(p: Pair, limit: int = 2) -> str:
    t = p.truth
    base = "; ".join(t.reasons[:limit]) or "-"
    return base + (" [%s]" % ",".join(sorted(t.tags)) if t.tags else "")


# ================================================================== 보고서
CLASS_LABEL = OrderedDict([
    ("OK", "일치"), ("FALSE_REJECT", "거짓 탈락 (엔진 미달 / 정답은 미달 아님)"), ("FALSE_ACCEPT_MATCH", "거짓 통과 (엔진 충족 / 정답은 미달 확정)"),
    ("OVERCONFIDENT", "과신 (엔진 충족 / 정답은 불확실)"), ("FALSE_ACCEPT_COND", "조건부로 통과 (엔진 조건부 / 정답은 미달 확정)"),
    ("KNOWN_LIMIT", "알려진 한계 (세부 지역·AI 구조화 누락)"), ("UNDERCONFIDENT", "과소 확신 (엔진 조건부 / 정답은 충족)")])


def format_report(pairs: Sequence[Pair], show: str = "problems", width: int = 120, style: str = "ascii", notices: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """show: problems(심각·경미한 불일치) / all(알려진 한계·과소 확신까지)"""
    from .table import render_table, truncate
    students = {p.student.id for p in pairs}
    schools = list(OrderedDict.fromkeys(p.school for p in pairs))
    idxs = list(OrderedDict.fromkeys(p.idx for p in pairs))
    L = ["=== 일치 시험: 공고 {} × 학생 {} × 학교 {} = {}쌍 ===".format(len(idxs), len(students), len(schools), len(pairs))]
    c = counts(pairs)
    rows = [[CLASS_LABEL[k], str(c.get(k, 0)), "{:.1f}%".format(100.0 * c.get(k, 0) / max(len(pairs), 1))] for k in ORDER]
    L += ["", render_table(["분류", "쌍", "비율"], rows, max_width=width, style=style, align=["l", "r", "r"])]
    cov = coverage(pairs)
    eng: Dict[str, Counter] = {}
    bad: Dict[str, Counter] = {}
    for p in pairs:
        eng.setdefault(p.idx, Counter())[p.verdict] += 1
        if p.klass != "OK":
            bad.setdefault(p.idx, Counter())[p.klass] += 1
    rows = []
    for idx in idxs:
        t = (notices or {}).get(idx, {}).get("title") or next((p.title for p in pairs if p.idx == idx), "")
        k, e = cov[idx], eng[idx]
        rows.append([idx, truncate(t, 28), "{}/{}/{}".format(k.get(NO, 0), k.get(UNSURE, 0), k.get(YES, 0)), "{}/{}/{}".format(e.get(matcher.NO_MATCH, 0), e.get(matcher.CONDITIONAL, 0), e.get(matcher.MATCH, 0)),
                     ", ".join("{} {}".format(CLASS_LABEL[kk].split(" ")[0], v) for kk, v in bad.get(idx, {}).items()) or "-"])
    L += ["", "[공고별] 정답 미달/불확실/충족 vs 엔진 미달/조건부/충족", render_table(["글번호", "공고", "정답", "엔진", "불일치"], rows, max_width=width, style=style)]
    shown = problems(pairs, include_info=(show == "all"))
    L += ["", "[불일치] {}쌍 (같은 원인은 학교를 묶어 표시)".format(len(shown))]
    if not shown:
        L.append("  없음")
        return "\n".join(L)
    groups: "OrderedDict[tuple, List[Pair]]" = OrderedDict()
    for p in sorted(shown, key=lambda q: (ORDER.index(q.klass), q.idx, q.student.id, q.school)):
        groups.setdefault((p.klass, p.idx, p.student.id, p.verdict, p.truth.outcome), []).append(p)
    rows = []
    for (klass, idx, sid, verdict, outcome), ps in groups.items():
        sch = ", ".join(q.school for q in ps) if len(ps) < len(schools) else "전체 학교"
        rows.append([CLASS_LABEL[klass].split(" ")[0], sid, idx, sch, VERDICT_KO[verdict], engine_reasons(ps[0], 1), TRUTH_KO[outcome], truth_reasons(ps[0], 1)])
    L.append(render_table(["분류", "학생", "공고", "학교", "엔진", "엔진 근거", "정답", "정답 근거"], rows, max_width=width, style=style))
    return "\n".join(L)
