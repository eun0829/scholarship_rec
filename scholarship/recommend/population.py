# -*- coding: utf-8 -*-
"""[RECOMMEND] 정확도 점검용 가상 학생: 조합 생성기(고정 시드, 같은 입력이면 같은 학생)와 대표 학생(data/personas/*.txt).

실제 학생 정보가 아니다. 값이 없는 항목(모름)도 일부러 섞어, 코드가 '모름'을 올바르게 다루는지 같이 본다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .profile import StudentProfile, build_profile
from .student_text import profile_from_text

REGIONS = [("경기도", "시흥시"), ("경기도", "수원시"), ("경기도", "안산시"), ("서울특별시", "노원구"), ("서울특별시", "은평구"), ("인천광역시", "부평구"),
           ("부산광역시", "해운대구"), ("충청남도", "논산시"), ("충청남도", "천안시"), ("강원특별자치도", "원주시"), ("전북특별자치도", "전주시"),
           ("대구광역시", "수성구"), ("제주특별자치도", "제주시")]
GROUPS = ["ENGINEERING"] * 8 + ["NATURAL", "SOCIAL", "HUMANITIES", "ARTS_PE", "AGRICULTURE"]
SPECIAL = [None] * 12 + ["BASIC_LIVELIHOOD", "NEAR_POOR", "SINGLE_PARENT", "DISABLED", "MULTICULTURAL", "VETERAN"]
MAJORS = {"ENGINEERING": ["컴퓨터공학부", "전자공학부", "기계공학과", "신소재공학과", "디자인공학부", "에너지전기공학과"], "NATURAL": ["수학과", "물리학과"],
          "SOCIAL": ["경영학과"], "HUMANITIES": ["국어국문학과"], "ARTS_PE": ["체육교육과", "디자인학과"], "AGRICULTURE": ["식물자원학과"]}
LANGS = [None, None, {"TOEIC": 600}, {"TOEIC": 780}, {"TOEIC": 900}, {"TOEIC": 780, "HSK": 4}]


@dataclass
class Student:
    id: str
    desc: str
    profile: StudentProfile


def default_personas_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "personas"


def load_personas(path: Optional[str] = None) -> List[Student]:
    out: List[Student] = []
    for f in sorted(Path(path or default_personas_dir()).glob("student_*.txt")):
        text = f.read_text(encoding="utf-8")
        built, _ = profile_from_text(text)
        first = text.splitlines()[0].lstrip("# ").strip() if text else f.stem
        out.append(Student("P" + f.stem.split("_", 1)[1], first, built.profile))
    return out


def _data(rng: random.Random, i: int) -> Dict[str, Any]:
    grad = rng.random() < 0.08
    grade = rng.choice([1, 2, 3, 4]) if not grad else rng.choice([1, 2])
    done = max(0, (grade - 1) * 2 + rng.choice([0, 1]))
    d: Dict[str, Any] = {"level": "GRAD" if grad else "UNIV", "gpa_scale": 4.5, "grade": grade, "semesters_completed": done,
                         "status": "ENROLLED" if rng.random() < 0.94 else rng.choice(["LEAVE", "GRADUATED"]),
                         "gender": rng.choice(["M", "F"]), "major_group": rng.choice(GROUPS), "nationality": "KR" if rng.random() < 0.96 else "FOREIGN",
                         "entry_year": 2026 - (done // 2), "admission_type": "TRANSFER" if rng.random() < 0.06 else "NEW"}
    if done > 0:
        g = round(rng.choice([2.0, 2.4, 2.8, 3.1, 3.4, 3.7, 4.0, 4.3]) + rng.uniform(-0.1, 0.1), 2)
        d["gpa_last"] = min(4.5, max(0.5, g))
        d["gpa_cumulative"] = round(min(4.5, max(0.5, g + rng.uniform(-0.3, 0.3))), 2)
        d["credits_last"] = rng.choice([9, 12, 15, 18, 21])
    d["major"] = rng.choice(MAJORS.get(d["major_group"], ["컴퓨터공학부"]))             # 학교 DB 값: 거의 항상 있다
    d["graduation_deferred"] = rng.random() < 0.05
    d["tuition_fully_waived"] = rng.random() < 0.08
    if done > 0 and rng.random() < 0.85:                                             # 100점 환산 점수: 학생이 알면 입력 (평점에서 대략 환산)
        d["gpa_percent"] = round(min(100.0, 55 + d["gpa_last"] * 10), 1)
        d["gpa_cumulative_percent"] = round(min(100.0, 55 + d["gpa_cumulative"] * 10), 1)
    if rng.random() < 0.85:
        d["income_bracket"] = rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    sido, sgg = rng.choice(REGIONS)
    d["residence_sido"], d["residence_sigungu"] = sido, sgg
    if rng.random() < 0.75:
        d["parent_residence_sido"], d["parent_residence_sigungu"] = (sido, sgg) if rng.random() < 0.7 else rng.choice(REGIONS)
    if rng.random() < 0.6:
        d["hs_sido"], d["hs_sigungu"] = rng.choice(REGIONS)
    if rng.random() < 0.7:
        d["residence_years"] = rng.choice([1, 3, 5])
    if rng.random() < 0.92:
        d["age"] = 19 + done // 2 + rng.choice([0, 1, 2, 3, 5])
    sp = rng.choice(SPECIAL)
    d["special_codes"] = [sp] if sp else []
    d["special_complete"] = True
    lang = rng.choice(LANGS)
    if lang:
        d["language_scores"] = lang
    if rng.random() < 0.5:
        d["national_scholarship_applied"] = rng.random() < 0.7
    return d


def generate(n: int = 250, seed: int = 20261014) -> List[Student]:
    """n 명의 가상 학생. 만들 수 없는 조합(검증 오류)은 건너뛰고 새로 뽑는다."""
    rng = random.Random(seed)
    out: List[Student] = []
    i = 0
    while len(out) < n and i < n * 5:
        i += 1
        d = _data(rng, i)
        try:
            p = build_profile(d).profile
        except Exception:
            continue
        sp = d["special_codes"][0] if d["special_codes"] else "-"
        out.append(Student("G{:03d}".format(len(out) + 1), "{} {}학년 평점 {} 소득 {} {} 신분 {}".format(
            d["level"], d["grade"], d.get("gpa_last", "없음"), d.get("income_bracket", "모름"), d["residence_sido"], sp), p))
    return out


def region_probes(notices: Dict[str, Dict[str, Any]], limit: int = 60) -> List[Student]:
    """공고가 요구하는 지역마다 그곳에 사는 우수한 가상 학생을 하나씩. 조합 학생에 없는 지역(대전, 거창 등)의 공고가 '아무도 못 받음'으로 잘못 보이지 않게 한다."""
    from . import places, vocab
    seen, out = set(), []
    blocks = []
    for n in notices.values():
        e = n["elig"]
        blocks += [e] + [v for v in e.get("variants") or [] if isinstance(v, dict)]
    for b in blocks:
        for rq in (b.get("region") or {}).get("requirements") or []:
            for a in rq.get("areas") or []:
                if not isinstance(a, str) or a in seen:
                    continue
                pl = places.parse_place(a)
                sido = vocab.canon_sido(a.split()[0]) if a.split() else None
                if not sido or pl.group or pl.foreign or pl.domestic:
                    continue
                seen.add(a)
                rest = " ".join(a.split()[1:]) or None
                d = {"level": "UNIV", "gpa_scale": 4.5, "grade": 3, "semesters_completed": 5, "status": "ENROLLED", "gender": "F", "major_group": "ENGINEERING",
                     "nationality": "KR", "gpa_last": 4.0, "gpa_cumulative": 3.9, "credits_last": 18, "income_bracket": 3, "age": 22, "residence_years": 5,
                     "residence_sido": sido, "parent_residence_sido": sido, "hs_sido": sido}
                if rest:
                    d["residence_sigungu"] = d["parent_residence_sigungu"] = d["hs_sigungu"] = rest
                try:
                    p = build_profile(d).profile
                except Exception:
                    continue
                out.append(Student("R{:03d}".format(len(out) + 1), "지역 프로브 {}".format(a), p))
                if len(out) >= limit:
                    return out
    return out
